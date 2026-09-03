## Toggle Side-Bar and Side-Panel

### Why use setSizes() instead of just setVisible?

QSplitter.setSizes() controls the actual width distribution.  
When cou vall setVisible(False) on a widget inside a splitter, the splitter may or may not resize proplerly depending on the widget's size policy.  
By explicitly setting sizes, you control exactly how the space is redistributed.

One issue: setSizes uses relative proportions, not absolute pixels.  
If your window is 1400px wide, [250, 575, 575] means "250 parts, 575 parts, 575 parts"  
The actual pixel widths are proportional. So these numbers are ratios, not exact pixels.

### Why Iterate Bottom-to-Top When Inserting

Lines:        Insert "# " at line 0 first:
  0: print()    → 0: # print()     Line 1 is still at index 1
  1: x = 1         1: x = 1         (correct)
  2: y = 2         2: y = 2

Now insert at line 1:  But wait — inserting text at line 0 shifted
everything down by 0 lines (insertAt doesn't add newlines, it inserts
at a position within a line). So line indices are still correct.

Actually, insertAt("# ", line, 0) inserts at the START of the line
without adding a newline. So line numbers DON'T shift. You CAN go
top-to-bottom. But going bottom-to-top is still safer because if
your logic changes later (e.g. adding full-line inserts), it won't
break.

## terminal_widget.py

### def _start_shell

1. if args is None: args = [] -- Default to empty argument list if none provided.  
2. if self.process.state() != QProcess.NotRunning: -- Check if a previous shell is still running. QProcess.state() returns QProcess.NotRunning, QProcess.Starting, or QProcess.Running. QProcess.state docs  
3. self.process.kill() -- Kill the previous shell process immediately. `kill()` sends `SIGKILL` on Linux or `TerminateProcess `on Windows. QProcess.kill docs  
4. `self.process.waitForFinished(2000)`-- Block for up to 2 seconds waiting for the process to actually die, so we don't start a new one before the old one is cleaned up.  
5. `QProcessEnvironment.systemEnvironment()`--- Creates a copy of the current system environment (PATH, TEMP, HOME, ect.) QProcessEnvironment.systemEnvironment docs.  
6. `if sys.platform != "win32":` -- On Linux/macOS, set the `Term` environment variable. Shell like bash and zsh check `TERM` to determine if they're running in a terminal and wheter to show a prompt. Without `TERM`, some shells produce no output. `xterm-256color` is a widely supported terminal type that enables 256 colors.