"""Tests for GNMA COBOL format parsing.

Tests for the COBOL format notation parser and value converter.
"""

from __future__ import annotations


class TestParseCobolFormat:
    """Tests for parse_cobol_format function."""

    def test_parse_string_format(self):
        """Test parsing string format X(n)."""
        from mortgage_data_manager.gnma.schemas.cobol import parse_cobol_format

        result = parse_cobol_format("X(9)")
        assert result is not None
        assert result["python_type"] == str
        assert result["total_length"] == 9
        assert result["cobol_type"] == "alphanumeric"

    def test_parse_single_char_format(self):
        """Test parsing single character format X."""
        from mortgage_data_manager.gnma.schemas.cobol import parse_cobol_format

        result = parse_cobol_format("X")
        assert result is not None
        assert result["python_type"] == str
        assert result["total_length"] == 1

    def test_parse_single_char_format_with_parens(self):
        """Test parsing single character format X(1)."""
        from mortgage_data_manager.gnma.schemas.cobol import parse_cobol_format

        result = parse_cobol_format("X(1)")
        assert result is not None
        assert result["python_type"] == str
        assert result["total_length"] == 1

    def test_parse_integer_format(self):
        """Test parsing integer format 9(n)."""
        from mortgage_data_manager.gnma.schemas.cobol import parse_cobol_format

        result = parse_cobol_format("9(13)")
        assert result is not None
        assert result["python_type"] == int
        assert result["total_length"] == 13
        assert result["cobol_type"] == "numeric_integer"

    def test_parse_decimal_format(self):
        """Test parsing decimal format 9(m)v9(n) with lowercase v."""
        from mortgage_data_manager.gnma.schemas.cobol import parse_cobol_format

        result = parse_cobol_format("9(13)v9(2)")
        assert result is not None
        assert result["python_type"] == float
        assert result["cobol_type"] == "numeric_decimal"
        assert result["decimal_places"] == 2
        assert result["total_length"] == 15  # 13 + 2

    def test_parse_decimal_format_four_places(self):
        """Test parsing decimal format with 4 decimal places."""
        from mortgage_data_manager.gnma.schemas.cobol import parse_cobol_format

        result = parse_cobol_format("9(9)v9(4)")
        assert result is not None
        assert result["python_type"] == float
        assert result["decimal_places"] == 4
        assert result["total_length"] == 13  # 9 + 4

    def test_parse_invalid_format(self):
        """Test parsing invalid format returns None."""
        from mortgage_data_manager.gnma.schemas.cobol import parse_cobol_format

        result = parse_cobol_format("INVALID")
        assert result is None

        result = parse_cobol_format("")
        assert result is None

    def test_parse_has_dtype_mappings(self):
        """Test that parsed format includes dtype mappings."""
        from mortgage_data_manager.gnma.schemas.cobol import parse_cobol_format

        result = parse_cobol_format("X(9)")
        assert "pandas_dtype" in result
        assert "polars_dtype" in result

        result = parse_cobol_format("9(10)")
        assert result["pandas_dtype"] == "Int64"

        result = parse_cobol_format("9(13)v9(2)")
        assert result["pandas_dtype"] == "float64"


class TestConvertCobolValue:
    """Tests for convert_cobol_value function."""

    def test_convert_string_value(self):
        """Test converting string value."""
        from mortgage_data_manager.gnma.schemas.cobol import convert_cobol_value, parse_cobol_format

        format_info = parse_cobol_format("X(9)")
        result = convert_cobol_value("ABC123   ", format_info)
        assert result == "ABC123   "  # Alphanumeric is returned as-is

    def test_convert_integer_value(self):
        """Test converting integer value."""
        from mortgage_data_manager.gnma.schemas.cobol import convert_cobol_value, parse_cobol_format

        format_info = parse_cobol_format("9(10)")
        result = convert_cobol_value("0000001234", format_info)
        assert result == 1234

    def test_convert_decimal_value(self):
        """Test converting decimal value."""
        from mortgage_data_manager.gnma.schemas.cobol import convert_cobol_value, parse_cobol_format

        # Value "123456789012345" with format 9(13)v9(2) should be 1234567890123.45
        format_info = parse_cobol_format("9(13)v9(2)")
        result = convert_cobol_value("123456789012345", format_info)
        assert isinstance(result, float)
        assert result == 1234567890123.45

    def test_convert_decimal_with_leading_zeros(self):
        """Test converting decimal with leading zeros."""
        from mortgage_data_manager.gnma.schemas.cobol import convert_cobol_value, parse_cobol_format

        format_info = parse_cobol_format("9(5)v9(2)")
        result = convert_cobol_value("0012345", format_info)
        assert result == 123.45

    def test_convert_invalid_value(self):
        """Test converting invalid value returns None."""
        from mortgage_data_manager.gnma.schemas.cobol import convert_cobol_value, parse_cobol_format

        format_info = parse_cobol_format("9(10)")
        result = convert_cobol_value("ABC123", format_info)
        assert result is None

    def test_convert_none_format_returns_original(self):
        """Test that None format returns original value."""
        from mortgage_data_manager.gnma.schemas.cobol import convert_cobol_value

        result = convert_cobol_value("test value", None)
        assert result == "test value"


class TestValidateCobolValue:
    """Tests for validate_cobol_value function."""

    def test_validate_valid_string(self):
        """Test validating valid string value."""
        from mortgage_data_manager.gnma.schemas.cobol import (
            parse_cobol_format,
            validate_cobol_value,
        )

        format_info = parse_cobol_format("X(9)")
        result = validate_cobol_value("ABCD12345", format_info)
        assert result["is_valid"] is True

    def test_validate_valid_integer(self):
        """Test validating valid integer value."""
        from mortgage_data_manager.gnma.schemas.cobol import (
            parse_cobol_format,
            validate_cobol_value,
        )

        format_info = parse_cobol_format("9(10)")
        result = validate_cobol_value("0000012345", format_info)
        assert result["is_valid"] is True

    def test_validate_invalid_integer(self):
        """Test validating invalid integer value."""
        from mortgage_data_manager.gnma.schemas.cobol import (
            parse_cobol_format,
            validate_cobol_value,
        )

        format_info = parse_cobol_format("9(10)")
        result = validate_cobol_value("ABC123DEF0", format_info)
        assert result["is_valid"] is False

    def test_validate_value_wrong_length(self):
        """Test validating value with wrong length."""
        from mortgage_data_manager.gnma.schemas.cobol import (
            parse_cobol_format,
            validate_cobol_value,
        )

        format_info = parse_cobol_format("X(9)")
        result = validate_cobol_value("ABC", format_info)
        assert result["is_valid"] is False


class TestCreateDtypeMapping:
    """Tests for dtype mapping creation."""

    def test_create_pandas_dtype_mapping(self, sample_schema_df):
        """Test creating pandas dtype mapping from schema."""
        from mortgage_data_manager.gnma.schemas.cobol import create_pandas_dtype_mapping

        mapping = create_pandas_dtype_mapping(sample_schema_df)
        assert isinstance(mapping, dict)

    def test_create_polars_dtype_mapping(self, sample_schema_df):
        """Test creating polars dtype mapping from schema."""
        from mortgage_data_manager.gnma.schemas.cobol import create_polars_dtype_mapping

        mapping = create_polars_dtype_mapping(sample_schema_df)
        assert isinstance(mapping, dict)
