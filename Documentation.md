## Toggle Side-Bar and Side-Panel

### Why use setSizes() instead of just setVisible?

QSplitter.setSizes() controls the actual width distribution.  
When cou vall setVisible(False) on a widget inside a splitter, the splitter may or may not resize proplerly depending on the widget's size policy.  
By explicitly setting sizes, you control exactly how the space is redistributed.

One issue: setSizes uses relative proportions, not absolute pixels.  
If your window is 1400px wide, [250, 575, 575] means "250 parts, 575 parts, 575 parts"  
The actual pixel widths are proportional. So these numbers are ratios, not exact pixels.