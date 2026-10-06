"""Let a tab widget inside a dock actually be made narrow.

THE BUG THIS FIXES, and why it was invisible on Linux. A `QTabBar`'s
minimum width depends on whether it may show scroll arrows:

    style            usesScrollButtons   tab-bar minimum (9 tabs)
    Fusion / Windows        True              133 px
    macOS                   False             524 px

macOS sets `SH_TabBar_PreferNoArrows`, so Qt refuses to give the bar
scroll buttons and instead makes its MINIMUM the width of every tab laid
out end to end. A dock containing that tab widget then cannot be dragged
narrower than the whole tab bar -- on the Properties panel, with nine
tabs, roughly 530 px of a window that the user wanted to give to the
diagram. The panel simply stops resizing, with no indication why.

It never reproduced on the Fusion style -- which is what the offscreen
test platform uses, and what the README screenshots were captured with,
so the arrows were present there and the panel shrank happily.

`setUsesScrollButtons(True)` overrides the style hint (the hint is only
consulted as the DEFAULT, in `QTabBarPrivate::init`), which drops the
minimum back to ~137 px and restores the arrows. Measured on both
styles, not assumed.
"""

from __future__ import annotations

from PySide6.QtWidgets import QTabWidget


def allow_narrow_resize(tabs: QTabWidget) -> QTabWidget:
    """Make `tabs` shrinkable regardless of platform style.

    Returns the same widget, so it can wrap a constructor call.

    Scroll buttons are forced ON rather than left to the style: they are
    what makes a narrow tab bar navigable, and without them the hidden
    tabs would be unreachable rather than merely scrolled past.
    """
    bar = tabs.tabBar()
    bar.setUsesScrollButtons(True)
    # `expanding` off keeps tabs their natural width rather than
    # stretching to fill -- already the case where this is used, and set
    # here too so the behaviour travels with the helper.
    bar.setExpanding(False)
    # A tab bar can still refuse to shrink below its own sizeHint unless
    # it is allowed to be smaller than its content; 0 lets the layout
    # give it whatever the dock has, with the scroll buttons covering
    # the rest.
    bar.setMinimumWidth(0)
    tabs.setMinimumWidth(0)
    return tabs
