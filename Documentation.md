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