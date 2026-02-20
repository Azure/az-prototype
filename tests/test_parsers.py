"""Tests for azext_prototype.parsers.file_extractor."""

from pathlib import Path

from azext_prototype.parsers.file_extractor import parse_file_blocks, write_parsed_files


# ======================================================================
# parse_file_blocks
# ======================================================================


class TestParseFileBlocks:
    """Unit tests for parse_file_blocks()."""

    def test_single_file_block(self):
        content = (
            "Here is the code:\n"
            "```main.tf\n"
            'resource "azurerm_resource_group" "rg" {}\n'
            "```\n"
        )
        result = parse_file_blocks(content)
        assert result == {"main.tf": 'resource "azurerm_resource_group" "rg" {}'}

    def test_multiple_file_blocks(self):
        content = (
            "```main.tf\n"
            "# main\n"
            "```\n"
            "\n"
            "```variables.tf\n"
            "# vars\n"
            "```\n"
        )
        result = parse_file_blocks(content)
        assert result == {"main.tf": "# main", "variables.tf": "# vars"}

    def test_nested_directory_paths(self):
        content = (
            "```infra/modules/network.tf\n"
            "# network\n"
            "```\n"
        )
        result = parse_file_blocks(content)
        assert "infra/modules/network.tf" in result

    def test_language_prefix_stripped(self):
        content = (
            "```python:src/app.py\n"
            "print('hello')\n"
            "```\n"
        )
        result = parse_file_blocks(content)
        assert "src/app.py" in result
        assert result["src/app.py"] == "print('hello')"

    def test_hcl_language_prefix(self):
        content = (
            "```hcl:main.tf\n"
            "resource {}\n"
            "```\n"
        )
        result = parse_file_blocks(content)
        assert "main.tf" in result

    def test_no_file_blocks_returns_empty(self):
        content = (
            "This is just prose.\n"
            "\n"
            "No code blocks here.\n"
        )
        result = parse_file_blocks(content)
        assert result == {}

    def test_code_block_without_filename_skipped(self):
        content = (
            "```python\n"
            "print('hello')\n"
            "```\n"
        )
        # "python" has no dot or slash, so it should be skipped
        result = parse_file_blocks(content)
        assert result == {}

    def test_unclosed_trailing_block(self):
        content = (
            "```output.json\n"
            '{"key": "value"}\n'
        )
        result = parse_file_blocks(content)
        assert "output.json" in result
        assert result["output.json"].strip() == '{"key": "value"}'

    def test_multiline_content(self):
        content = (
            "```main.py\n"
            "import os\n"
            "import sys\n"
            "\n"
            "def main():\n"
            "    pass\n"
            "```\n"
        )
        result = parse_file_blocks(content)
        assert "main.py" in result
        lines = result["main.py"].split("\n")
        assert lines[0] == "import os"
        assert lines[1] == "import sys"
        assert lines[3] == "def main():"

    def test_mixed_file_and_non_file_blocks(self):
        content = (
            "Here is an example:\n"
            "```bash\n"
            "echo hello\n"
            "```\n"
            "\n"
            "And the actual file:\n"
            "```deploy.sh\n"
            "#!/bin/bash\n"
            "```\n"
        )
        result = parse_file_blocks(content)
        # "bash" has no dot/slash → skipped; "deploy.sh" has a dot → parsed
        assert list(result.keys()) == ["deploy.sh"]

    def test_four_backtick_fence(self):
        content = (
            "````main.tf\n"
            "resource {}\n"
            "````\n"
        )
        result = parse_file_blocks(content)
        assert "main.tf" in result

    def test_empty_string(self):
        assert parse_file_blocks("") == {}

    def test_empty_file_block(self):
        content = (
            "```empty.txt\n"
            "```\n"
        )
        result = parse_file_blocks(content)
        assert result == {"empty.txt": ""}

    def test_whitespace_around_filename(self):
        content = (
            "```  main.tf  \n"
            "resource {}\n"
            "```\n"
        )
        result = parse_file_blocks(content)
        assert "main.tf" in result

    def test_consecutive_blocks_no_gap(self):
        content = (
            "```a.tf\n"
            "aaa\n"
            "```\n"
            "```b.tf\n"
            "bbb\n"
            "```\n"
        )
        result = parse_file_blocks(content)
        assert result == {"a.tf": "aaa", "b.tf": "bbb"}


# ======================================================================
# write_parsed_files
# ======================================================================


class TestWriteParsedFiles:
    """Unit tests for write_parsed_files()."""

    def test_writes_single_file(self, tmp_path: Path):
        files = {"hello.txt": "Hello, world!"}
        written = write_parsed_files(files, tmp_path, verbose=False)
        assert len(written) == 1
        assert written[0].read_text(encoding="utf-8") == "Hello, world!"

    def test_creates_subdirectories(self, tmp_path: Path):
        files = {"a/b/c.txt": "deep"}
        written = write_parsed_files(files, tmp_path, verbose=False)
        assert len(written) == 1
        assert (tmp_path / "a" / "b" / "c.txt").exists()
        assert written[0].read_text(encoding="utf-8") == "deep"

    def test_multiple_files(self, tmp_path: Path):
        files = {"one.txt": "1", "two.txt": "2", "three.txt": "3"}
        written = write_parsed_files(files, tmp_path, verbose=False)
        assert len(written) == 3
        for p in written:
            assert p.exists()

    def test_verbose_output(self, tmp_path: Path, capsys):
        files = {"app.py": "pass"}
        write_parsed_files(files, tmp_path, verbose=True, label="infra")
        captured = capsys.readouterr()
        assert "infra/app.py" in captured.out

    def test_empty_files_dict(self, tmp_path: Path):
        written = write_parsed_files({}, tmp_path, verbose=False)
        assert written == []

    def test_output_dir_created(self, tmp_path: Path):
        new_dir = tmp_path / "does_not_exist"
        files = {"test.txt": "content"}
        write_parsed_files(files, new_dir, verbose=False)
        assert new_dir.exists()
        assert (new_dir / "test.txt").read_text(encoding="utf-8") == "content"


# ======================================================================
# Integration: parse → write
# ======================================================================


class TestParseAndWrite:
    """End-to-end tests that parse AI output and write to disk."""

    def test_full_pipeline(self, tmp_path: Path):
        ai_output = (
            "# Generated Infrastructure\n\n"
            "```main.tf\n"
            'resource "azurerm_resource_group" "rg" {\n'
            '  name     = "rg-demo"\n'
            '  location = "eastus"\n'
            "}\n"
            "```\n\n"
            "```variables.tf\n"
            'variable "location" {\n'
            '  default = "eastus"\n'
            "}\n"
            "```\n\n"
            "```outputs.tf\n"
            "output \"rg_name\" {\n"
            '  value = azurerm_resource_group.rg.name\n'
            "}\n"
            "```\n"
        )
        files = parse_file_blocks(ai_output)
        assert len(files) == 3
        assert "main.tf" in files
        assert "variables.tf" in files
        assert "outputs.tf" in files

        written = write_parsed_files(files, tmp_path, verbose=False)
        assert len(written) == 3
        for p in written:
            assert p.exists()
            assert p.stat().st_size > 0

    def test_no_files_detected_writes_nothing(self, tmp_path: Path):
        ai_output = "Just a summary with no code blocks."
        files = parse_file_blocks(ai_output)
        assert files == {}
        written = write_parsed_files(files, tmp_path, verbose=False)
        assert written == []
