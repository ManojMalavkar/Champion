# TUI assets

Put the logo files here (names are fixed, see `champion/tui/logo.py`):

* `m_logo.png` - the still M logo (Idle). Drawn with half blocks when Pillow is installed; a white or transparent background becomes the terminal background.
* `m_logo.gif` - the animated M (Thinking and Working), used from T9.2.

Without these files, or without Pillow, the header shows a text M in the same blue-to-cyan gradient. Error always shows the text M in red.
