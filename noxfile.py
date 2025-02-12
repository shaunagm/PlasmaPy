"""
Nox is an automation tool used by PlasmaPy to run tests, build
documentation, and perform other checks. Nox sessions are defined in
noxfile.py.

Running `nox` without arguments will run tests with the version of
Python that `nox` is installed under, skipping slow tests. To invoke a
nox session, enter the top-level directory of this repository and run
`nox -s "<session>"`, where <session> is replaced with the name of the
session. To list available sessions, run `nox -l`.

The tests can be run with the following options:

* "all": run all tests
* "skipslow": run tests, except tests decorated with `@pytest.mark.slow`
* "cov": run all tests with code coverage checks
* "lowest-direct" : run all tests with lowest version of direct dependencies

Doctests are run only for the most recent versions of Python and
PlasmaPy dependencies, and not when code coverage checks are performed.
Some of the checks require the most recent supported version of Python
to be installed.
"""

import os
import pathlib
import sys
from typing import Literal

import nox

supported_python_versions: tuple[str, ...] = ("3.10", "3.11", "3.12")

maxpython = max(supported_python_versions)
minpython = min(supported_python_versions)

current_python = f"{sys.version_info.major}.{sys.version_info.minor}"

nox.options.sessions: list[str] = [f"tests-{current_python}(skipslow)"]
nox.options.default_venv_backend = "uv|virtualenv"

running_on_ci = os.getenv("CI")


def _get_requirements_filepath(
    category: Literal["docs", "tests", "all"],
    version: Literal["3.10", "3.11", "3.12", "3.13", "3.14", "3.15"],
    resolution: Literal["highest", "lowest-direct", "lowest"] = "highest",
) -> str:
    """
    Return the file path to the requirements file.

    Parameters
    ----------
    category : str
        The name of the optional dependency set, as defined in
        :file:`pyproject.toml`.

    version : str
        The supported version of Python.

    resolution : str
        The resolution strategy used by uv.
    """
    requirements_directory = "ci_requirements"
    specifiers = [category, version]
    if resolution != "highest":
        specifiers.append(resolution)
    return f"{requirements_directory}/{'-'.join(specifiers)}.txt"


@nox.session
def requirements(session) -> None:
    """
    Regenerate the pinned requirements files used in CI.

    This session uses `uv pip compile` to regenerate the pinned
    requirements files in `ci_requirements/` for use by the Nox sessions
    for running tests, building documentation, and performing other
    continuous integration checks.
    """

    session.install("uv >= 0.2.23")

    category_version_resolution: list[tuple[str, str, str]] = [
        ("tests", version, "highest") for version in supported_python_versions
    ]

    category_version_resolution += [
        ("tests", minpython, "lowest-direct"),
        ("docs", maxpython, "highest"),
        ("all", maxpython, "highest"),
    ]

    category_flags: dict[str, tuple[str, ...]] = {
        "all": ("--all-extras",),
        "docs": ("--extra", "docs"),
        "tests": ("--extra", "tests"),
    }

    command: tuple[str, ...] = (
        "python",
        "-m",
        "uv",
        "pip",
        "compile",
        "pyproject.toml",
        "--upgrade",
        "--quiet",
        "--custom-compile-command",  # defines command to be included in file header
        "nox -s requirements",
    )

    for category, version, resolution in category_version_resolution:
        filename = _get_requirements_filepath(category, version, resolution)
        session.run(
            *command,
            "--python-version",
            version,
            *category_flags[category],
            "--output-file",
            filename,
            "--resolution",
            resolution,
            *session.posargs,
        )


pytest_command: tuple[str, ...] = (
    "pytest",
    "--pyargs",
    "--durations=5",
    "--tb=short",
    "-n=auto",
    "--dist=loadfile",
)

with_doctests: tuple[str, ...] = ("--doctest-modules", "--doctest-continue-on-failure")

with_coverage: tuple[str, ...] = (
    "--cov=plasmapy",
    "--cov-report=xml",
    "--cov-config=pyproject.toml",
    "--cov-append",
    "--cov-report",
    "xml:coverage.xml",
)

skipslow: tuple[str, ...] = ("-m", "not slow")

test_specifiers: list = [
    nox.param("run all tests", id="all"),
    nox.param("skip slow tests", id="skipslow"),
    nox.param("with code coverage", id="cov"),
    nox.param("lowest-direct", id="lowest-direct"),
]


@nox.session(python=supported_python_versions)
@nox.parametrize("test_specifier", test_specifiers)
def tests(session: nox.Session, test_specifier: nox._parametrize.Param) -> None:
    """Run tests with pytest."""

    resolution = "lowest-direct" if test_specifier == "lowest-direct" else "highest"

    requirements = _get_requirements_filepath(
        category="tests",
        version=session.python,
        resolution=resolution,
    )

    options: list[str] = []

    if test_specifier == "skip slow tests":
        options += skipslow

    if test_specifier == "with code coverage":
        options += with_coverage

    # Doctests are only run with the most recent versions of Python and
    # other dependencies because there may be subtle differences in the
    # output between different versions of Python, NumPy, and Astropy.
    if session.python == maxpython and test_specifier in {"all", "skipslow"}:
        options += with_doctests

    if gh_token := os.getenv("GH_TOKEN"):
        session.env["GH_TOKEN"] = gh_token

    session.install("-r", requirements, ".[tests]")
    session.run(*pytest_command, *options, *session.posargs)


@nox.session(python=maxpython)
@nox.parametrize(
    ["repository"],
    [
        nox.param("numpy", id="numpy"),
        nox.param("https://github.com/astropy/astropy", id="astropy"),
        nox.param("https://github.com/pydata/xarray", id="xarray"),
        nox.param("https://github.com/lmfit/lmfit-py", id="lmfit"),
        nox.param("https://github.com/pandas-dev/pandas", id="pandas"),
    ],
)
def run_tests_with_dev_version_of(session: nox.Session, repository: str) -> None:
    """
    Run tests against the development branch of a dependency.

    Running this session helps us catch problems resulting from breaking
    changes in an upstream dependency before its official release.
    """
    if repository != "numpy":
        session.install(f"git+{repository}")
    else:
        # From: https://numpy.org/doc/1.26/dev/depending_on_numpy.html
        session.run_install(
            "uv",
            "pip",
            "install",
            "-U",
            "--pre",
            "--only-binary",
            ":all:",
            "-i",
            "https://pypi.anaconda.org/scientific-python-nightly-wheels/simple",
            "numpy",
        )
    session.install(".[tests]")
    session.run(*pytest_command, *session.posargs)


sphinx_commands: tuple[str, ...] = (
    "sphinx-build",
    "docs/",
    "docs/build/html",
    "--nitpicky",
    "--fail-on-warning",
    "--keep-going",
    "-q",
)

build_html: tuple[str, ...] = ("--builder", "html")
check_hyperlinks: tuple[str, ...] = ("--builder", "linkcheck")
docs_requirements = _get_requirements_filepath(category="docs", version=maxpython)

doc_troubleshooting_message = """

📘 Tips for troubleshooting common documentation build failures are in
PlasmaPy's documentation guide at:

🔗 https://docs.plasmapy.org/en/latest/contributing/doc_guide.html#troubleshooting
"""


@nox.session(python=maxpython)
def docs(session: nox.Session) -> None:
    """
    Build documentation with Sphinx.

    This session may require installation of pandoc and graphviz.
    """
    if running_on_ci:
        session.debug(doc_troubleshooting_message)
    session.install("-r", docs_requirements, ".")
    session.run(*sphinx_commands, *build_html, *session.posargs)
    landing_page = (
        pathlib.Path(session.invoked_from) / "docs" / "build" / "html" / "index.html"
    )

    if not running_on_ci and landing_page.exists():
        session.debug(f"The documentation may be previewed at {landing_page}")
    elif not running_on_ci:
        session.debug(f"Documentation preview landing page not found: {landing_page}")


@nox.session(python=maxpython)
@nox.parametrize(
    ["site", "repository"],
    [
        nox.param("github", "sphinx-doc/sphinx", id="sphinx"),
        nox.param("github", "readthedocs/sphinx_rtd_theme", id="sphinx_rtd_theme"),
        nox.param("github", "spatialaudio/nbsphinx", id="nbsphinx"),
    ],
)
def build_docs_with_dev_version_of(
    session: nox.Session, site: str, repository: str
) -> None:
    """
    Build documentation against the development branch of a dependency.

    The purpose of this session is to catch bugs and breaking changes
    so that they can be fixed or updated earlier rather than later.
    """
    session.install(f"git+https://{site}.com/{repository}", ".[docs]")
    session.run(*sphinx_commands, *build_html, *session.posargs)


LINKCHECK_TROUBLESHOOTING = """
The Sphinx configuration variables `linkcheck_ignore` and
`linkcheck_allowed_redirects` in `docs/conf.py` can be used to specify
hyperlink patterns to be ignored along with allowed redirects. For more
information, see:

🔗 https://www.sphinx-doc.org/en/master/usage/configuration.html#confval-linkcheck_ignore
🔗 https://www.sphinx-doc.org/en/master/usage/configuration.html#confval-linkcheck_allowed_redirects

These variables are in the form of Python regular expressions:

🔗 https://docs.python.org/3/howto/regex.html
"""


@nox.session(python=maxpython)
def linkcheck(session: nox.Session) -> None:
    """Check hyperlinks in documentation."""
    if running_on_ci:
        session.debug(LINKCHECK_TROUBLESHOOTING)
    session.install("-r", docs_requirements)
    session.install(".")
    session.run(*sphinx_commands, *check_hyperlinks, *session.posargs)


MYPY_TROUBLESHOOTING = """
🛡 To learn more about type hints, check out mypy's cheat sheet at:
  https://mypy.readthedocs.io/en/stable/cheat_sheet_py3.html

For more details about specific mypy errors, go to:
🔗 https://mypy.readthedocs.io/en/stable/error_codes.html

🪧 Especially difficult errors can be ignored with an inline comment of
the form: `# type: ignore[error]`, where `error` is replaced with the
mypy error code. Please use sparingly!

🛠 To automatically add type hints for common patterns, run:
  nox -s 'autotyping(safe)'
"""


@nox.session(python=maxpython)
def mypy(session: nox.Session) -> None:
    """Perform static type checking."""
    if running_on_ci:
        session.debug(MYPY_TROUBLESHOOTING)
    MYPY_COMMAND: tuple[str, ...] = (
        "mypy",
        ".",
        "--install-types",
        "--non-interactive",
        "--show-error-context",
        "--show-error-code-links",
        "--pretty",
    )

    requirements = _get_requirements_filepath(
        category="tests",
        version=session.python,
        resolution="highest",
    )
    session.install("pip")
    session.install("-r", requirements, ".[tests]")
    session.run(*MYPY_COMMAND, *session.posargs)


@nox.session(name="import")
def try_import(session: nox.Session) -> None:
    """Install PlasmaPy and import it."""
    session.install(".")
    session.run("python", "-c", "import plasmapy", *session.posargs)


@nox.session
def build(session: nox.Session) -> None:
    """Build & verify the source distribution and wheel."""
    session.install("twine", "build")
    build_command = ("python", "-m", "build")
    session.run(*build_command, "--sdist")
    session.run(*build_command, "--wheel")
    session.run("twine", "check", "dist/*", *session.posargs)


AUTOTYPING_SAFE: tuple[str, ...] = (
    "--none-return",
    "--scalar-return",
    "--annotate-magics",
)
AUTOTYPING_RISKY: tuple[str, ...] = (
    *AUTOTYPING_SAFE,
    "--bool-param",
    "--int-param",
    "--float-param",
    "--str-param",
    "--bytes-param",
    "--annotate-imprecise-magics",
)


@nox.session
@nox.parametrize(
    "options",
    [
        nox.param(AUTOTYPING_SAFE, id="safe"),
        nox.param(AUTOTYPING_RISKY, id="aggressive"),
    ],
)
def autotyping(session: nox.Session, options: tuple[str, ...]) -> None:
    """
    Automatically add type hints with autotyping.

    The `safe` option generates very few incorrect type hints, and can
    be used in CI. The `aggressive` option may add type hints that are
    incorrect, so please perform a careful code review when using this
    option.

    To check specific files, pass them after a `--`, such as:

        nox -s 'autotyping(safe)' -- noxfile.py
    """
    session.install(".[tests,docs]", "autotyping", "typing_extensions")
    DEFAULT_PATHS = ("src", "tests", "tools", "*.py", ".github", "docs/*.py")
    paths = session.posargs or DEFAULT_PATHS
    session.run("python", "-m", "autotyping", *options, *paths)


@nox.session
def cff(session: nox.Session) -> None:
    """Validate CITATION.cff against the metadata standard."""
    session.install("cffconvert")
    session.run("cffconvert", "--validate", *session.posargs)


@nox.session
def manifest(session: nox.Session) -> None:
    """
    Check for missing files in MANIFEST.in.

    When run outside of CI, this check may report files that were
    locally created but not included in version control. These false
    positives can be ignored by adding file patterns and paths to
    `ignore` under `[tool.check-manifest]` in `pyproject.toml`.
    """
    session.install("check-manifest")
    session.run("check-manifest", *session.posargs)


@nox.session
def lint(session: nox.Session) -> None:
    """Run all pre-commit hooks on all files."""
    session.install("pre-commit")
    session.run(
        "pre-commit",
        "run",
        "--all-files",
        "--show-diff-on-failure",
        *session.posargs,
    )
