"""Tests for azext_prototype.ui.console — targeting uncovered lines."""

from unittest.mock import patch


class TestConsolePrintError:
    """Cover Console.print_error (line 98)."""

    def test_print_error_includes_message(self):
        from azext_prototype.ui.console import Console

        c = Console()
        with patch.object(c._console, "print") as mock_print:
            c.print_error("something broke")
            mock_print.assert_called_once()
            output = mock_print.call_args[0][0]
            assert "something broke" in output
            assert "[error]" in output


class TestConsoleClearLastLine:
    """Cover Console.clear_last_line (lines 115-116)."""

    def test_clear_last_line_writes_ansi(self):
        from azext_prototype.ui.console import Console

        c = Console()
        with patch("azext_prototype.ui.console.sys.stdout") as mock_stdout:
            c.clear_last_line()
            mock_stdout.write.assert_called_once_with("\033[A\033[2K\r")
            mock_stdout.flush.assert_called_once()


class TestConsoleStatus:
    """Cover Console.status context manager (lines 216-217)."""

    def test_status_delegates_to_spinner(self):
        from azext_prototype.ui.console import Console

        c = Console()
        with patch.object(c._console, "print"):
            with c.status("working..."):
                pass  # just exercise the context manager


class TestConsoleProgressFiles:
    """Cover Console.progress_files context manager (lines 173-183)."""

    def test_progress_files_yields_progress_object(self):
        from rich.progress import Progress

        from azext_prototype.ui.console import Console

        c = Console()
        with c.progress_files("Reading") as progress:
            assert isinstance(progress, Progress)


class TestDiscoveryPromptSimple:
    """Cover DiscoveryPrompt.simple_prompt (lines 462-465)."""

    def test_simple_prompt_returns_stripped_input(self):
        from azext_prototype.ui.console import Console, DiscoveryPrompt

        c = Console()
        dp = DiscoveryPrompt(c)
        with patch.object(dp._session, "prompt", return_value="  hello  "):
            result = dp.simple_prompt("> ")
            assert result == "hello"

    def test_simple_prompt_eof_returns_empty(self):
        from azext_prototype.ui.console import Console, DiscoveryPrompt

        c = Console()
        dp = DiscoveryPrompt(c)
        with patch.object(dp._session, "prompt", side_effect=EOFError):
            result = dp.simple_prompt("> ")
            assert result == ""

    def test_simple_prompt_keyboard_interrupt_returns_empty(self):
        from azext_prototype.ui.console import Console, DiscoveryPrompt

        c = Console()
        dp = DiscoveryPrompt(c)
        with patch.object(dp._session, "prompt", side_effect=KeyboardInterrupt):
            result = dp.simple_prompt("> ")
            assert result == ""


class TestDiscoveryPromptEOF:
    """Cover DiscoveryPrompt.prompt EOFError path (lines 443-445)."""

    def test_prompt_eof_returns_empty(self):
        from azext_prototype.ui.console import Console, DiscoveryPrompt

        c = Console()
        dp = DiscoveryPrompt(c)
        with patch.object(dp._session, "prompt", side_effect=EOFError), patch.object(c._console, "print"):
            result = dp.prompt("> ")
            assert result == ""


class TestDiscoveryPromptNoQuitHint:
    """Cover the no-quit-hint toolbar branch (lines 431-435)."""

    def test_prompt_no_quit_hint_toolbar(self):
        from azext_prototype.ui.console import Console, DiscoveryPrompt

        c = Console()
        dp = DiscoveryPrompt(c)
        with patch.object(dp._session, "prompt", return_value="test") as mock_prompt, patch.object(c._console, "print"):
            dp.prompt("> ", show_quit_hint=False)
            # Verify the toolbar callable was passed
            call_kwargs = mock_prompt.call_args[1]
            toolbar_fn = call_kwargs["bottom_toolbar"]
            # Exercise the toolbar function (covers lines 431-435)
            result = toolbar_fn()
            assert isinstance(result, list)
            assert len(result) == 1  # border only, no hint line
