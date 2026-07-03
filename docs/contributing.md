# Contributing to Mortgage Data Manager

Thank you for your interest in contributing to Mortgage Data Manager!

## Development Setup

### 1. Clone the Repository

```bash
git clone <repository-url>
cd mortgage_data_manager
```

### 2. Install Dependencies

We use `uv` for dependency management:

```bash
# Install uv if needed
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install for development with all dependencies
uv pip install -e ".[all,dev]"
```

### 3. Set Up Pre-commit Hooks (Recommended)

```bash
uv pip install pre-commit
pre-commit install
```

## Project Structure

```
mortgage_data_manager/
├── src/mortgage_data_manager/  # Main package
│   ├── core/                   # Shared utilities (config, medallion, I/O, logging)
│   ├── cli/                    # Unified CLI entry point
│   ├── hmda/                   # HMDA subpackage
│   ├── fha/                    # FHA subpackage
│   ├── gnma/                   # GNMA (Ginnie Mae) subpackage
│   ├── fhfa/                   # FHFA subpackage
│   ├── fnma/                   # Fannie Mae subpackage
│   ├── fhlmc/                  # Freddie Mac subpackage
│   ├── umbs/                   # UMBS subpackage
│   ├── hud/                    # HUD single-family subpackage
│   ├── hud_mf/                 # HUD multifamily subpackage
│   ├── fhlb/                   # FHLB subpackage
│   ├── analytics/              # Derived analytics
│   ├── combined/               # Cross-source master datasets
│   └── matching/               # Matching workflows
├── tests/                      # Test suite
├── docs/                       # Documentation
├── data/                       # Data directory (not in repo)
└── output/                     # Output directory (not in repo)
```

## Making Changes

### 1. Create a Branch

```bash
git checkout -b feature/your-feature-name
```

### 2. Make Your Changes

Follow these guidelines:

#### Code Style
- Follow PEP 8 style guide
- Use type hints for function parameters and returns
- Add docstrings to all public functions and classes
- Keep functions focused and single-purpose

#### Documentation
- Update relevant documentation in `docs/`
- Add docstrings using Google or NumPy style
- Include examples for new features

#### Tests
- Add tests for new functionality
- Ensure all tests pass: `pytest tests/`
- Maintain or improve code coverage

### 3. Run Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src/mortgage_data_manager --cov-report=html

# Run specific test module
pytest tests/test_core/test_config.py -v
```

### 4. Check Code Quality

```bash
# Run linters
ruff check .

# Format code
ruff format .

# Type checking
mypy src/
```

### 5. Commit Changes

Use descriptive commit messages:

```bash
git add .
git commit -m "Add feature: description of change"
```

Follow conventional commits format:
- `feat:` - New feature
- `fix:` - Bug fix
- `docs:` - Documentation changes
- `test:` - Test changes
- `refactor:` - Code refactoring
- `chore:` - Maintenance tasks

## Adding New Features

### Adding a New Subpackage

1. Create subpackage directory: `src/mortgage_data_manager/new_source/`
2. Implement config: `NewSourceConfig(MortgageDataConfig)` in `config.py`
3. Add medallion processing:
   - `download.py` -> raw layer
   - `import_bronze.py` -> bronze layer
   - `import_silver.py` -> silver layer
4. Create CLI in `cli/main.py` using Typer
5. Register in unified CLI: `cli/main.py:cli_main()`
6. Add optional dependencies to `pyproject.toml`
7. Create API documentation in `docs/api/`

### Adding CLI Commands

Follow the standardized CLI pattern:

```python
from mortgage_data_manager import __version__
from mortgage_data_manager.core.cli.formatting import console, print_info_table
from mortgage_data_manager.core.cli.validation import version_callback_factory

_version_callback = version_callback_factory("My Data Manager", __version__)

app = typer.Typer(name="mydata", help="My data commands")

@app.callback()
def callback(
    version: Annotated[bool, typer.Option("--version", "-V", callback=_version_callback, is_eager=True)] = False,
):
    pass

@app.command()
def info():
    """Display configuration information."""
    print_info_table("My Configuration", {"Key": "Value"})
```

### Adding New Data Source Support

1. Create configuration class inheriting from `MortgageDataConfig`
2. Add download function if needed
3. Add import/cleaning pipeline following medallion architecture
4. Update documentation

## Testing Guidelines

### Writing Tests

- Use pytest fixtures from `tests/conftest.py`
- Test both success and failure cases
- Use descriptive test names
- Group related tests in classes

Example:

```python
class TestMyFeature:
    """Test my new feature."""

    def test_feature_with_valid_input(self, sample_data):
        """Test feature works with valid input."""
        result = my_function(sample_data)
        assert result is not None

    def test_feature_with_invalid_input(self):
        """Test feature handles invalid input gracefully."""
        with pytest.raises(ValueError):
            my_function(None)
```

### Running Tests

```bash
# All tests
pytest

# Specific file
pytest tests/test_core/test_config.py

# Specific test
pytest tests/test_core/test_config.py::TestConfig::test_load_data

# With coverage
pytest --cov=src/mortgage_data_manager --cov-report=html

# Stop on first failure
pytest -x

# Verbose output
pytest -v
```

## Documentation Guidelines

### Docstring Format

Use Google-style docstrings:

```python
def my_function(param1: str, param2: int) -> bool:
    """
    Brief description of what the function does.
    
    More detailed description if needed. Can span multiple
    lines and include examples.
    
    Args:
        param1: Description of param1
        param2: Description of param2
    
    Returns:
        Description of return value
    
    Raises:
        ValueError: When invalid input is provided
    
    Example:
        >>> result = my_function("test", 42)
        >>> print(result)
        True
    """
    pass
```

### Updating Documentation

When adding features, update:
- Relevant guide in `docs/usage/`
- API reference in `docs/api/`
- Main README if needed

## Pull Request Process

1. **Update tests and documentation**
2. **Ensure all tests pass**
3. **Update CHANGELOG.md** (if applicable)
4. **Create pull request** with description:
   - What changes were made
   - Why the changes were needed
   - How to test the changes
5. **Address review feedback**

## Code Review Guidelines

When reviewing code, check for:
- Correct functionality
- Test coverage
- Documentation completeness
- Code style consistency
- Performance considerations
- Security implications

## Release Process

(For maintainers)

1. Update version in `pyproject.toml`
2. Update CHANGELOG.md
3. Create git tag: `git tag v0.x.x`
4. Push tag: `git push origin v0.x.x`
5. Build and publish: `uv build && uv publish`

## Questions or Issues?

- Check existing issues in the repository
- Ask questions in discussions
- Reach out to maintainers

## License

By contributing, you agree that your contributions will be licensed under the same license as the project (see LICENSE file).

## Thank You!

Your contributions make this project better for everyone in the mortgage data research community.

