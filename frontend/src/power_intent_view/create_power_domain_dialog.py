""""Create Power Domain" dialog (and its own nested "Create Supply Set"
dialog) -- triggered from the Power Intent tab's Diagram right-click
"Create Power Domain"/"Edit Power Domain" actions (see
power_intent_diagram_view.py's _extra_context_menu_actions() override).
Diagram-only for this pass, same scoping decision as supply ports (see
power_domain_overlay.py's own module docstring) -- these dialogs only
ever hand back plain data (name/colour/transparency/supply-set
references/newly-introduced supply-port colours), never touch shared
state themselves; the view's own create_power_domain()/
edit_power_domain() does that, same division of responsibility
AddPowerSupplyDialog already established.

The same dialog class serves both Create and Edit: passing `initial`
(an existing PowerDomainPlacement) and `editing_domain_id` pre-populates
every field and excludes that domain's own name from the uniqueness
check at accept() time -- otherwise editing a domain without renaming
it would spuriously collide with itself.

`CreateSupplySetDialog`'s own accept() flow (via `_prompt_create_and_assign_supply_set()` below) validates a newly-created Supply Set two ways, with DELIBERATELY DIFFERENT scopes (bug fix, explicit policy correction -- a previous version checked the pin-combination rule GLOBALLY too, incorrectly rejecting two unrelated Power Domains that happen to reuse the same pin-naming convention, e.g. VDD/VSS, for their own independent rails): its name must still be unique across every OTHER Power Domain, GLOBALLY (Fix 3C,
`power_domain_overlay.supply_set_name_used_by_another_domain()`), but its full Power/Ground/N-Well/P-Well pin combination must not exactly duplicate another Supply Set's ONLY WITHIN THIS SAME DOMAIN (`power_domain_overlay.supply_set_with_duplicate_pin_combination()`, now called against `_this_domains_supply_sets()` -- this domain's own current Primary/Secondary only, not every Supply Set in the design) -- its original purpose (avoiding an ambiguous tie between a domain's own Primary and Secondary) never needed a wider scope than that. PARTIAL overlap (sharing exactly one rail) stays legal, only a full 4-field match blocks.

CHANGE 2 (explicit request): the "New Supply Set" button is now a
"Supply Set" DROPDOWN (`supply_set_combo`) offering every Supply Set
that already exists on a STRICT ANCESTOR hierarchy of the current
block (`power_domain_overlay.supply_sets_on_ancestor_hierarchies_for_owners()`
-- NOT sibling, NOT descendant) alongside "Add New...". Picking an
ancestor Supply Set opens the SAME `CreateSupplySetDialog`, pre-filled
and fully LOCKED (`locked=True` -- every field but "Set as Primary" is
read-only) from that Supply Set's OWN existing values; accepting it
SHARES the exact same SupplySetPlacement id as this domain's own
Primary/Secondary -- NOT a clone, confirmed as the correct model
because `PowerDomainPlacement.primary_supply_set`/
`secondary_supply_sets` already reference a Supply Set by id, so two
different `PowerDomainPlacement`s referencing the SAME id is already
structurally supported with no schema change (see
`_prompt_assign_ancestor_supply_set()`'s own docstring). This is a
GENUINELY DIFFERENT mechanism from the pre-existing multi-hierarchy
Power Domain replication (`additional_boxes`/`owner_paths` on ONE
domain, each member hierarchy getting its OWN auto-created Supply
Port under the same name) -- ancestor sharing never touches a Supply
Set's own `owner_paths` at all (it stays exactly where it was
originally composed), so it can never trigger
`_add_missing_supply_ports()`/`_auto_link_cross_hierarchy_supply_sets()`'s
own per-owner-path materialization for the new (sharing) domain's own
hierarchy -- the two mechanisms coexist without interference by
construction, not by an added exclusion check.
"""

from __future__ import annotations

from typing import Callable, Optional
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from frontend.src.power_intent_view.add_power_supply_dialog import AddPowerSupplyDialog
from frontend.src.power_intent_view.name_validation import NO_SPACES_ERROR, validate_no_spaces
from frontend.src.power_intent_view.power_domain_overlay import (
    DOMAIN_FIXED_TRANSPARENCY,
    PowerDomainPlacement,
    SupplySetPlacement,
    resolve_domain_color,
    supply_set_name_used_by_another_domain,
    supply_set_with_duplicate_pin_combination,
    supply_sets_on_ancestor_hierarchies_for_owners,
)
from frontend.src.power_intent_view.supply_port_overlay import SupplyPortIconItem, SupplyPortState
from frontend.src.shared.widgets import message_box
from frontend.src.shared.widgets.form_separator import form_separator
from frontend.src.shared.widgets.target_hierarchy_label import APPLIES_TO_CAPTION, make_target_hierarchy_label

_DEFAULT_COLOR = QColor("#4caf50")
_ADD_NEW_NAME = "Add New..."


class _SupplyNameCombo(QComboBox):
    """A supply-name picker: every currently-known supply name (box-
    scoped -- see supply_port_overlay.py's own
    known_supply_names_for_owner()) plus "Add New..." -- explicit
    request: "if power supply doesn't exist, ask user to add new ones".
    Picking "Add New..." pops up the exact same AddPowerSupplyDialog
    standalone "Add Power Supply" itself uses (explicit request: "so
    user can set name and colour as a standalone port creation") --
    name *and* colour chosen together, in one dialog, rather than the
    name here and the colour deferred to some later step. Cancelling
    reverts to whatever was selected before. `on_new_supply`, if given,
    is called with (name, colour, states) so the owning
    CreateSupplySetDialog can track it across all 4 of its own combos.

    Bug fix (explicit report): `states` used to be silently dropped
    here -- any Power State entered in the popped-up AddPowerSupplyDialog
    before clicking OK never reached `on_new_supply` at all (only
    `dialog.name`/`dialog.color` were ever read), so a Supply Port
    created this way -- through "Create Power Domain -> Create Supply
    Set -> Add New", the ONLY way to introduce a brand-new Supply Port
    from within this flow -- ended up with an empty states list
    regardless of what was actually typed into the dialog's own Power
    States table. Fixed by reading `dialog.states` too, right alongside
    `dialog.name`/`dialog.color`, and threading it the rest of the way
    through CreateSupplySetDialog.new_supply_port_states ->
    CreatePowerDomainDialog.new_supply_port_states ->
    _add_missing_supply_ports() -- see this module's own
    CreateSupplySetDialog._register_new_supply() and
    power_intent_diagram_view.py's own _add_missing_supply_ports().

    `is_internal_supply` (bug fix -- the resulting supply, once actually
    materialized by _add_missing_supply_ports(), becomes an Internal
    Supply whenever the domain/strategy's own owner box isn't the
    design's true top-level block, regardless of anything chosen in
    THIS dialog): threaded down from the owning CreateSupplySetDialog so
    the popped-up AddPowerSupplyDialog shows the correct mode -- an
    Internal Supply never gets an editable Power States section (see
    that dialog's own docstring), since its states are always derived,
    never user-authored."""

    def __init__(
        self,
        available_names: list[str],
        on_new_supply: Optional[Callable[[str, QColor, list[SupplyPortState]], None]] = None,
        parent: Optional[QWidget] = None,
        is_internal_supply: bool = False,
        target_path: Optional[str] = None,
    ):
        super().__init__(parent)
        self.addItem("")
        self.addItems(available_names)
        self.addItem(_ADD_NEW_NAME)
        self._previous_index = 0
        self._on_new_supply = on_new_supply
        self._is_internal_supply = is_internal_supply
        # Bug fix: threaded down from CreateSupplySetDialog (itself from
        # CreatePowerDomainDialog's own `owner_path`) so the nested
        # AddPowerSupplyDialog popped up by "Add New..." shows the SAME
        # "Applies to: <hierarchy>" row the standalone Add Power Supply/
        # Add Internal Supply dialogs already do -- confirmed via a live
        # diagnostic that this exact row (never passed here before) was
        # the ONLY real HEIGHT discrepancy between the two (519x164 vs
        # 519x143), previously misdiagnosed as a still-unresolved WIDTH
        # issue.
        self._target_path = target_path
        self.currentIndexChanged.connect(self._on_index_changed)

    def _on_index_changed(self, index: int) -> None:
        if self.itemText(index) == _ADD_NEW_NAME:
            dialog = AddPowerSupplyDialog(self, target_path=self._target_path, is_internal_supply=self._is_internal_supply)
            # Bug fix: this nested dialog never updated its own window
            # title away from the class's own generic default ("Add
            # Power Supply") -- unlike every standalone entry point
            # (_prompt_add_power_supply()/_prompt_add_internal_supply()
            # in power_intent_diagram_view.py), which each set it
            # explicitly right after construction. Confirmed via a live
            # diagnostic that this was the ONLY actual divergence
            # between the standalone and nested dialogs for the Internal
            # Supply case -- the Power States/Inherited-States section
            # itself was already correct and identical in both.
            dialog.setWindowTitle("Add Internal Supply" if self._is_internal_supply else "Add Power Supply")
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.name:
                name, color, states = dialog.name, QColor(dialog.color), list(dialog.states)
                insert_at = self.count() - 1
                # blockSignals() around the insert+select pair -- a real
                # bug, found via a live repro: inserting a new item *at*
                # the current index (where "Add New..." itself sits)
                # shifts Qt's own current-index bookkeeping forward to
                # keep pointing at "Add New..." (now one slot later), and
                # that shift itself re-emits currentIndexChanged -- which
                # re-entered this same branch and popped the same
                # AddPowerSupplyDialog open a second time. Blocking here
                # and setting `_previous_index`/emitting `_on_new_supply`
                # explicitly afterwards has the identical end effect
                # without the re-entrant signal.
                self.blockSignals(True)
                self.insertItem(insert_at, name)
                self.setCurrentIndex(insert_at)
                self.blockSignals(False)
                self._previous_index = insert_at
                if self._on_new_supply is not None:
                    self._on_new_supply(name, color, states)
            else:
                self.blockSignals(True)
                self.setCurrentIndex(self._previous_index)
                self.blockSignals(False)
        else:
            self._previous_index = index

    def selected_name(self) -> Optional[str]:
        text = self.currentText()
        return text if text and text != _ADD_NEW_NAME else None

    def offer_name(self, name: str) -> None:
        """Makes `name` selectable here too, without changing the
        current selection or firing _on_index_changed -- used to mirror
        a name added via one of CreateSupplySetDialog's *other* 3
        combos into this one, so e.g. a name just added via Power is
        immediately choosable as N-Well too (needed for the "N-Well/
        P-Well must match either Power or Ground" check below to be
        satisfiable at all when the matching name is brand new)."""
        if self.findText(name) >= 0:
            return
        self.blockSignals(True)
        self.insertItem(self.count() - 1, name)
        self.blockSignals(False)


class CreateSupplySetDialog(QDialog):
    """Power/Ground are hard-required (explicit request); N-Well/P-Well
    are OPTIONAL (explicit follow-up request, superseding an earlier
    "all 4 hard-required" version -- see this class's own git history/
    the module docstring above) -- disabled by default when the dialog
    opens, unlocked via `enable_bias_supply_checkbox` ("Enable Bias
    Supply", positioned above them). Correction (explicit follow-up,
    superseding an earlier one-shot "Enable" BUTTON version of this
    exact feature): a genuine toggle, not a one-way unlock -- unchecking
    it after having populated N-Well/P-Well CLEARS both combos back to
    blank (`_on_wells_toggled()`), returning to the true "no well data"
    state, rather than merely disabling-while-preserving whatever was
    typed. Confirmed as the right call, not just the user's own
    suggestion taken on faith: `n_well`/`p_well` (below) read straight
    from each combo's OWN `selected_name()` regardless of its enabled
    state (see _SupplyNameCombo's own docstring -- a disabled combo's
    `currentText()` is still whatever it was last set to, Qt's
    `setEnabled()` only blocks USER interaction, never programmatic
    state) -- disabling without clearing would silently keep submitting
    a stale, no-longer-visible well value the user believes they
    retracted by unchecking the box, a real correctness bug this
    dialog's own "no wells at all is a fully valid, explicit state"
    contract depends on getting right. Whether ENABLED-but-left-blank
    or still disabled-and-cleared, an empty combo's own `selected_name()`
    already returns None either way -- no separate "is this field even
    unlocked" bookkeeping needed at the property level, a blank
    selection means the same "no well specified" thing regardless of
    why it's blank.

    Validation (accept()-time QMessageBox, same "not a perpetual
    OK-button disable" precedent every check here already follows):
    N-Well and P-Well must be populated TOGETHER or BOTH left empty --
    populating only one is rejected as an inconsistent, invalid state.
    Whenever BOTH are populated, each must still match either Power or
    Ground (real design convention -- N-well ties to the power net,
    P-well to the ground net, sometimes mixed on a triple-well process;
    a well net disjoint from *both* is rejected) -- that check simply
    never runs at all when both are empty, since there's nothing to
    validate against Power/Ground in that case.

    `new_port_colors` (name -> colour) is populated live, as each "Add
    New..." completes via `_register_new_supply()` -- not deferred to a
    batch of colour prompts at accept() time the way an earlier version
    of this dialog did. That deferred approach is exactly the "bug"
    (confusing, unexpected: clicking OK on a fully-filled-out form could
    still pop up one modal colour picker per new name introduced,
    disconnected from when each name was actually typed) the explicit
    request to "pop up the same window... so user can set name and
    colour as a standalone port creation" fixes -- see
    _SupplyNameCombo's own docstring.

    Fix 4A (explicit request): Primary vs. Secondary is no longer two
    separate creation flows on CreatePowerDomainDialog's own two
    combos -- ONE "Set as Primary" checkbox here decides it instead.
    `primary_available` (the caller's own `self._primary_id is None`
    at the moment "Create Supply Set..." was clicked) gates whether the
    checkbox can be checked at all: ENABLED only when the owning
    domain has no Primary yet, DISABLED (and forced unchecked) once one
    already exists -- see `set_as_primary`'s own docstring for why the
    property re-checks `isEnabled()` too, not just `isChecked()`."""

    def __init__(
        self,
        available_supply_names: list[str],
        parent: Optional[QWidget] = None,
        primary_available: bool = True,
        editing_supply_set_id: Optional[str] = None,
        initial: Optional[SupplySetPlacement] = None,
        is_internal_supply: bool = False,
        target_path: Optional[str] = None,
        locked: bool = False,
    ):
        super().__init__(parent)
        self.setSizeGripEnabled(True)  # explicit request (Bug 5): every dialog here must be user-resizable
        self._editing_supply_set_id = editing_supply_set_id
        self.setWindowTitle("Edit Supply Set" if editing_supply_set_id is not None else "Create Supply Set")
        self.new_port_colors: dict[str, QColor] = {}
        # Bug fix (explicit report): tracked alongside new_port_colors,
        # same first-write-wins convention -- see _register_new_supply()
        # and _SupplyNameCombo's own docstring for the full "why".
        self.new_port_states: dict[str, list[SupplyPortState]] = {}
        # CHANGE 2 (explicit request, ancestor Supply Set sharing):
        # `locked` requires `initial` (there'd be nothing to lock in
        # otherwise) -- every field EXCEPT "Set as Primary" becomes
        # read-only/disabled, since selecting an ancestor's EXISTING
        # Supply Set shares that exact object rather than composing a
        # new one -- see this class's own updated docstring and
        # CreatePowerDomainDialog._prompt_assign_ancestor_supply_set()'s
        # own docstring for the full "why".
        self._locked = locked
        self.name_edit = QLineEdit(initial.name if initial is not None else "")
        self.name_edit.setPlaceholderText("e.g. SS_VDD")
        # Bug fix: `target_path` (the hierarchy this Supply Set is being
        # composed on, threaded down from the owning CreatePowerDomainDialog/
        # PortAttributeDialog) is passed to every one of the 4 combos so
        # their own nested "Add New..." AddPowerSupplyDialog shows the
        # same "Applies to" row the standalone dialog does -- see
        # _SupplyNameCombo's own docstring.
        self.power_combo = _SupplyNameCombo(available_supply_names, on_new_supply=self._register_new_supply, is_internal_supply=is_internal_supply, target_path=target_path)
        self.ground_combo = _SupplyNameCombo(available_supply_names, on_new_supply=self._register_new_supply, is_internal_supply=is_internal_supply, target_path=target_path)
        self.n_well_combo = _SupplyNameCombo(available_supply_names, on_new_supply=self._register_new_supply, is_internal_supply=is_internal_supply, target_path=target_path)
        self.p_well_combo = _SupplyNameCombo(available_supply_names, on_new_supply=self._register_new_supply, is_internal_supply=is_internal_supply, target_path=target_path)
        # Explicit request: N-Well/P-Well start disabled -- optional,
        # unlocked only via enable_bias_supply_checkbox below.
        self.n_well_combo.setEnabled(False)
        self.p_well_combo.setEnabled(False)
        self.enable_bias_supply_checkbox = QCheckBox("Enable Bias Supply")
        self.enable_bias_supply_checkbox.setChecked(False)
        self.enable_bias_supply_checkbox.toggled.connect(self._on_wells_toggled)
        # Edit mode (explicit request, Properties-table Edit/Remove):
        # pre-fills Power/Ground/N-Well/P-Well from `initial` -- offer_name()
        # first guarantees each is selectable even if not already in
        # `available_supply_names` for some reason, then setCurrentText()
        # selects it. Checking enable_bias_supply_checkbox (AFTER both
        # well combos are pre-offered) unlocks them via the same
        # _on_wells_toggled() every other path already goes through --
        # no separate pre-fill-specific enabling logic needed.
        if initial is not None:
            for combo, value in (
                (self.power_combo, initial.power),
                (self.ground_combo, initial.ground),
                (self.n_well_combo, initial.n_well),
                (self.p_well_combo, initial.p_well),
            ):
                if value:
                    combo.offer_name(value)
                    combo.setCurrentText(value)
            if initial.n_well or initial.p_well:
                self.enable_bias_supply_checkbox.setChecked(True)
        if locked:
            # Disabling AFTER the pre-fill block above -- setEnabled()
            # never clears a combo's own current selection (Qt only
            # blocks further USER interaction), so the pre-filled
            # values stay exactly as offered/selected; disabling the
            # checkbox itself (not un-toggling it) means
            # _on_wells_toggled()'s own "unchecking clears" behavior
            # never fires here either.
            self.name_edit.setReadOnly(True)
            self.power_combo.setEnabled(False)
            self.ground_combo.setEnabled(False)
            self.n_well_combo.setEnabled(False)
            self.p_well_combo.setEnabled(False)
            self.enable_bias_supply_checkbox.setEnabled(False)
        # "Set as Primary" only makes sense in CreateSupplySetDialog's
        # ORIGINAL context (opened from a specific Power Domain that
        # knows whether a Primary is already assigned) -- editing a
        # Supply Set from the Properties table has no such single-
        # domain context (a Supply Set isn't inherently "the Primary of"
        # anything on its own), so this checkbox/row is omitted
        # entirely in edit mode rather than shown with no meaningful
        # domain to apply it to.
        self.set_as_primary_checkbox = QCheckBox("Set as Primary")
        self.set_as_primary_checkbox.setEnabled(primary_available)
        self.set_as_primary_checkbox.setChecked(False)  # opt-in, never assumed

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self._ok_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_button.setEnabled(bool(self.name_edit.text().strip()))
        self.name_edit.textChanged.connect(lambda text: self._ok_button.setEnabled(bool(text.strip())))

        form = QFormLayout(self)
        form.addRow("Name:", self.name_edit)
        form.addRow("Power:", self.power_combo)
        form.addRow("Ground:", self.ground_combo)
        form.addRow(self.enable_bias_supply_checkbox)
        form.addRow("N-Well:", self.n_well_combo)
        form.addRow("P-Well:", self.p_well_combo)
        if editing_supply_set_id is None:
            form.addRow(self.set_as_primary_checkbox)
        form.addRow(self.buttons)

    def _on_wells_toggled(self, checked: bool) -> None:
        self.n_well_combo.setEnabled(checked)
        self.p_well_combo.setEnabled(checked)
        if not checked:
            # Explicit request/correction: unchecking CLEARS back to the
            # true "no well data" state, rather than disabling-while-
            # preserving -- see this class's own docstring for why a
            # silent preserve-but-disabled combo would be a real
            # correctness bug here, not just a UX preference.
            self.n_well_combo.setCurrentIndex(0)
            self.p_well_combo.setCurrentIndex(0)

    @property
    def set_as_primary(self) -> bool:
        """False whenever the checkbox is disabled, regardless of its
        raw checkState() -- defense-in-depth (nothing can actually
        check a disabled checkbox through real UI interaction, but this
        keeps the property itself correct even if something else ever
        forces checkState() directly)."""
        return self.set_as_primary_checkbox.isEnabled() and self.set_as_primary_checkbox.isChecked()

    def _register_new_supply(self, name: str, color: QColor, states: list[SupplyPortState]) -> None:
        if name not in self.new_port_colors:
            self.new_port_colors[name] = color
            self.new_port_states[name] = states
        for combo in (self.power_combo, self.ground_combo, self.n_well_combo, self.p_well_combo):
            combo.offer_name(name)

    def accept(self) -> None:
        if not validate_no_spaces(self.name):
            message_box.warning(self, "Invalid Name", NO_SPACES_ERROR)
            return
        missing = [label for label, value in (("Power", self.power), ("Ground", self.ground)) if not value]
        if missing:
            message_box.warning(self, "Incomplete Supply Set", f"{', '.join(missing)} must be populated.")
            return
        # N-Well/P-Well are optional (explicit request), but all-or-
        # nothing -- populating only one leaves the pair in an
        # inconsistent state and is rejected, same as the pre-existing
        # Power/Ground "must be populated" check above in spirit, just
        # scoped to "together" rather than "always".
        if bool(self.n_well) != bool(self.p_well):
            message_box.warning(self, "Incomplete Well Pins", "N-Well and P-Well must both be set together, or both left empty.")
            return
        if self.n_well is not None and self.n_well not in (self.power, self.ground):
            message_box.warning(self, "Invalid N-Well", "N-Well must match either the Power supply or the Ground supply.")
            return
        if self.p_well is not None and self.p_well not in (self.power, self.ground):
            message_box.warning(self, "Invalid P-Well", "P-Well must match either the Power supply or the Ground supply.")
            return
        super().accept()

    @property
    def name(self) -> str:
        return self.name_edit.text().strip()

    @property
    def power(self) -> Optional[str]:
        return self.power_combo.selected_name()

    @property
    def ground(self) -> Optional[str]:
        return self.ground_combo.selected_name()

    @property
    def n_well(self) -> Optional[str]:
        return self.n_well_combo.selected_name()

    @property
    def p_well(self) -> Optional[str]:
        return self.p_well_combo.selected_name()


class CreatePowerDomainDialog(QDialog):
    """`existing_supply_sets`/`available_supply_names` are read-only
    inputs (a snapshot at dialog-open time); any Supply Set created
    during this dialog's own session (via the "Create Supply Set..."
    button) is tracked separately in `new_supply_sets`, and any
    supply-port *name* introduced along the way in `new_supply_ports`
    (name -> chosen colour) -- both only ever handed back to the caller
    on accept, this dialog never mutates shared state itself (see module
    docstring). Bug fix (explicit request): accept() itself prunes both
    down to only what's actually referenced by the FINAL configuration
    right before handing them back -- see `_discard_unused_new_entities()`
    -- so a Supply Set/supply name created and then abandoned mid-flow
    (removed via "Remove Selected", or superseded by a replacement name
    before its own inner dialog's OK) never reaches the diagram at all,
    matching a full Cancel of this whole dialog (already correctly
    inert, since the caller only ever reads these on Accepted).

    Primary Supply Set is a single slot, not a list: the "Create Supply
    Set..." flow below (Fix 4A) is the only way to set `_primary_id`
    (via its own "Set as Primary" checkbox) -- removing it via
    `_remove_selected_supply_set()` (from `supply_set_list`, "Primary: "/
    "Secondary: " prefixed, primary always listed first) is the only
    way to free the slot back up. Primary is a hard requirement at
    accept() time (explicit request), Secondary is not.

    Fix 4A (explicit request, superseding an earlier version where each
    of two separate "Primary Supply"/"Secondary Supply" picker combos
    offered its own "Create New Supply Set..." entry, and the newly-
    created set still needed an extra "Add" click to actually become
    Primary/Secondary): ONE standalone control opens `CreateSupplySetDialog`
    with a "Set as Primary" checkbox (enabled only when `_primary_id is
    None`) -- accepting it assigns the Supply Set as Primary or
    Secondary immediately, no separate "Add" step. CHANGE 2 (explicit
    request) turned that standalone control from a plain
    "Create Supply Set..." button into `supply_set_combo`, a dropdown
    also offering every ancestor hierarchy's own EXISTING Supply
    Set(s) as SHARING options alongside "Add New..." -- see this
    class's own module docstring and
    `_prompt_assign_ancestor_supply_set()`'s own docstring.

    Follow-up explicit request (further simplification): the "Primary
    Supply"/"Secondary Supply" picker fields (for attaching an already-
    EXISTING Supply Set, as opposed to creating a new one) are removed
    entirely -- "Create Supply Set..." is now the ONLY way to add a
    Supply Set to a domain, with Primary-vs-Secondary decided per-
    Supply-Set at creation time via its own checkbox, never as a
    separate top-level field on this dialog. `supply_set_list`/
    `remove_supply_set_button` are unaffected -- still the one place
    that shows and removes whatever's already been added.

    `existing_domain_names` (domain id -> name, every domain diagram-
    wide) backs the accept()-time "Power domain X already exists, choose
    a different name" check -- global, not scoped to any one hierarchy,
    per the explicit request. `editing_domain_id` excludes that one
    domain's own current name from the check, so Edit Power Domain
    without renaming doesn't collide with itself.

    `owner_path` (the box Create/Edit Power Domain was invoked on) is
    stamped onto any Supply Set created via this dialog's own "Create
    Supply Set..." flow -- see SupplySetPlacement.owner_path's own
    docstring for why.

    `owner_paths` (explicit request, multi-hierarchy Power Domain; new,
    OPTIONAL keyword-only parameter -- every pre-existing single-
    hierarchy caller/test that only ever passes `owner_path` keeps
    working completely unchanged) is the FULL list of hierarchy blocks
    this domain applies to -- defaults to `[owner_path]` alone when
    omitted. Shown read-only near the top of the dialog (explicit
    request: "Applies to: ...", shared with Retention/Supply Port/
    Internal Supply -- see shared/widgets/target_hierarchy_label.py) and
    stamped onto any Supply Set created inline here, so the resulting
    Supply Port/Internal Supply gets replicated across every member
    hierarchy (see power_intent_diagram_view.py's own
    _add_missing_supply_ports()).

    `all_domains`/`all_supply_sets` (Fix 3C, explicit request; both
    OPTIONAL, default to empty when omitted so every pre-existing
    caller/test that never cared about cross-domain uniqueness keeps
    working unchanged) are the FULL, diagram-wide domain/Supply-Set
    universe -- a strictly WIDER scope than `existing_supply_sets`
    (which stays scoped to this domain's own hierarchies, unaffected --
    the LIST offered when picking an EXISTING Supply Set is still
    scoped; only the NAME-uniqueness check on a newly-CREATED one
    widens). Used by `_prompt_create_and_assign_supply_set()` to reject
    a new Supply Set name already used by some OTHER Power Domain
    anywhere in the design -- see
    power_domain_overlay.py's own supply_set_name_used_by_another_domain()."""

    def __init__(
        self,
        existing_supply_sets: dict[str, SupplySetPlacement],
        available_supply_names: list[str],
        existing_domain_names: dict[str, str],
        owner_path: str,
        editing_domain_id: Optional[str] = None,
        initial: Optional[PowerDomainPlacement] = None,
        parent: Optional[QWidget] = None,
        owner_paths: Optional[list[str]] = None,
        all_domains: Optional[dict[str, PowerDomainPlacement]] = None,
        all_supply_sets: Optional[dict[str, SupplySetPlacement]] = None,
        supply_port_icons: Optional[dict[str, SupplyPortIconItem]] = None,
    ):
        super().__init__(parent)
        self.setSizeGripEnabled(True)  # explicit request (Bug 5): every dialog here must be user-resizable
        self.setWindowTitle("Edit Power Domain" if editing_domain_id is not None else "Create Power Domain")
        # Explicit bug report: this dialog appeared translucent, unlike
        # every other dialog here (Add Supply Port/Supply Set/Retention
        # Strategy). No stray setWindowOpacity()/alpha stylesheet/
        # WA_TranslucentBackground=True was actually found in this class
        # -- explicitly forcing both off/on here is the standard
        # corrective idiom for this symptom regardless of root cause
        # (e.g. a platform-level translucent-window default), matching
        # every other dialog's own effectively-opaque rendering.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAutoFillBackground(True)
        self._existing_supply_sets = dict(existing_supply_sets)
        self.new_supply_sets: dict[str, SupplySetPlacement] = {}
        self.new_supply_ports: dict[str, QColor] = {}
        # Bug fix (explicit report): tracked alongside new_supply_ports,
        # same "accumulate live, prune to referenced names at accept()"
        # lifecycle -- see _prompt_create_and_assign_supply_set() and
        # _prune_unreferenced_new_supply_ports() below.
        self.new_supply_port_states: dict[str, list[SupplyPortState]] = {}
        self._available_supply_names = list(available_supply_names)
        self._owner_path = owner_path
        # Bug fix: the supply materialized from this dialog's own inline
        # "Create Supply Set..." flow becomes an Internal Supply the
        # moment it's placed on anything but the design's true top-level
        # block (see power_intent_diagram_view.py's own
        # _add_missing_supply_ports()) -- so the nested AddPowerSupplyDialog
        # popped up while composing that Supply Set must never offer an
        # editable Power States section in that case (see this class's
        # own _prompt_create_and_assign_supply_set()).
        self._is_internal_target = "/" in owner_path
        self._owner_paths = list(owner_paths) if owner_paths else [owner_path]
        self._existing_domain_names = dict(existing_domain_names)
        self._editing_domain_id = editing_domain_id
        self._global_domains = dict(all_domains) if all_domains else {}
        self._global_supply_sets = dict(all_supply_sets) if all_supply_sets else {}
        self._supply_port_icons = dict(supply_port_icons) if supply_port_icons else {}
        self._fallback_color = QColor(initial.color) if initial is not None else QColor(_DEFAULT_COLOR)
        self._secondary_ids: list[str] = list(initial.secondary_supply_sets) if initial is not None else []
        self._primary_id: Optional[str] = initial.primary_supply_set if initial is not None else None

        self.name_edit = QLineEdit(initial.name if initial is not None else "")
        self.name_edit.setPlaceholderText("e.g. PD_CPU")

        # Explicit request, supersedes the earlier manual colour-picker/
        # transparency-spinbox pair entirely: a Power Domain's own colour
        # is no longer user-chosen -- it's LIVE-derived from its Primary
        # Supply Set's own Power rail colour, at a FIXED, non-adjustable
        # transparency (resolve_domain_color()/DOMAIN_FIXED_TRANSPARENCY
        # -- see power_domain_overlay.py's own docstring for the full
        # "why"). Bug fix (explicit follow-up request): this dialog shows
        # NOTHING about colour at all anymore -- no field, no read-only
        # preview, no note -- superseding an earlier version that kept a
        # read-only "Colour (from Primary Supply Set)" preview row; the
        # derivation itself still happens exactly the same, entirely
        # behind the scenes, in the view's own
        # _refresh_domain_derived_colors().

        # Fix 4A + follow-up simplification, then CHANGE 2 (explicit
        # request): ONE combo is the only way to add a Supply Set --
        # either an ancestor's EXISTING one (shared, not cloned) or
        # "Add New..." -- see this class's own docstring.
        self.supply_set_combo = QComboBox()
        self._ancestor_choices: dict[str, SupplySetPlacement] = {}
        self._refresh_supply_set_combo()
        self.supply_set_combo.currentIndexChanged.connect(self._on_supply_set_combo_changed)

        self.supply_set_list = QListWidget()
        self.remove_supply_set_button = QPushButton("Remove Selected")
        self.remove_supply_set_button.clicked.connect(self._remove_selected_supply_set)

        self._refresh_supply_set_list()

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self._ok_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_button.setEnabled(bool(self.name_edit.text().strip()))
        self.name_edit.textChanged.connect(lambda text: self._ok_button.setEnabled(bool(text.strip())))

        list_column = QVBoxLayout()
        list_column.addWidget(self.supply_set_list)
        list_column.addWidget(self.remove_supply_set_button)

        form = QFormLayout(self)
        form.addRow(APPLIES_TO_CAPTION, make_target_hierarchy_label(self._owner_paths))
        form.addRow(form_separator())
        form.addRow("Name:", self.name_edit)
        form.addRow("Supply Set:", self.supply_set_combo)
        form.addRow("Supply Sets:", list_column)
        form.addRow(self.buttons)

    # --- Supply Set pickers ---------------------------------------------

    def _all_supply_sets(self) -> dict[str, SupplySetPlacement]:
        """CHANGE 2 (explicit request): now layered from
        `self._global_supply_sets` FIRST (the full diagram-wide
        universe) -- an ancestor-SHARED Supply Set's own id may not be
        in `self._existing_supply_sets` at all (that dict stays scoped
        to THIS domain's own hierarchy/hierarchies, per
        supply_sets_for_owner()'s own docstring, while a shared id
        lives at the ANCESTOR's), so resolving it here (for display,
        colour-preview, etc.) needs the wider pool. `self._existing_supply_sets`/
        `self.new_supply_sets` are always subsets of the same live
        design-wide dict already, so layering them on top changes
        nothing for every pre-existing (non-shared) caller."""
        combined = dict(self._global_supply_sets)
        combined.update(self._existing_supply_sets)
        combined.update(self.new_supply_sets)
        return combined

    def _this_domains_supply_sets(self) -> dict[str, SupplySetPlacement]:
        """Bug fix (explicit policy correction): the Supply Sets THIS
        domain (being created/edited right now) currently references as
        its own Primary or Secondary -- `self._primary_id`/
        `self._secondary_ids`, resolved against `_all_supply_sets()` so
        a Supply Set created earlier in this SAME dialog session (not
        yet in `self._existing_supply_sets`, which is a snapshot taken
        at construction time) still resolves. This is the correct,
        NARROWER scope for supply_set_with_duplicate_pin_combination()
        -- see the module docstring's own "bug fix" paragraph for why
        design-wide was wrong."""
        all_sets = self._all_supply_sets()
        ids = [sid for sid in (self._primary_id, *self._secondary_ids) if sid is not None]
        return {sid: all_sets[sid] for sid in ids if sid in all_sets}

    def _refresh_supply_set_combo(self) -> None:
        """CHANGE 2 (explicit request): rebuilds `supply_set_combo`'s
        own item list -- a blank placeholder, every ancestor Supply Set
        not YET referenced by this domain (excluding one already
        assigned as Primary/Secondary -- re-offering it would let a
        second selection assign the SAME id as Secondary right after
        it's already Primary, a nonsensical duplicate reference), and
        "Add New..." last. Each ancestor item's own `itemData` is its
        real SupplySetPlacement id (`Qt.ItemDataRole.UserRole`, Qt's
        own default for `addItem(text, userData)`) -- resolved back by
        `_on_supply_set_combo_changed()`, not by re-matching display
        text. Called at construction and after every assignment (so an
        ancestor Supply Set just picked immediately disappears from
        the list of further choices)."""
        self.supply_set_combo.blockSignals(True)
        self.supply_set_combo.clear()
        self.supply_set_combo.addItem("")
        already_referenced = {sid for sid in (self._primary_id, *self._secondary_ids) if sid}
        ancestor_sets = supply_sets_on_ancestor_hierarchies_for_owners(self._global_supply_sets, self._owner_paths)
        self._ancestor_choices = {sid: s for sid, s in ancestor_sets.items() if sid not in already_referenced}
        for supply_set_id, supply_set in sorted(self._ancestor_choices.items(), key=lambda kv: kv[1].name):
            self.supply_set_combo.addItem(supply_set.name, supply_set_id)
        self.supply_set_combo.addItem(_ADD_NEW_NAME)
        self.supply_set_combo.setCurrentIndex(0)
        self.supply_set_combo.blockSignals(False)

    def _on_supply_set_combo_changed(self, index: int) -> None:
        """Blank placeholder (index 0) is a no-op. "Add New..." routes
        to `_prompt_create_and_assign_supply_set()` (unchanged). Any
        other selection is an ancestor Supply Set's own real id, routed
        to `_prompt_assign_ancestor_supply_set()`. The combo is reset
        back to the blank placeholder afterward EITHER way (accepted or
        cancelled) -- same "revert to a neutral selection so picking
        the same entry again still fires a change" precedent
        `_SupplyNameCombo`'s own Cancel-path already establishes --
        `blockSignals()` around it so that reset itself doesn't
        recurse back into this same handler."""
        if index <= 0:
            return
        text = self.supply_set_combo.itemText(index)
        if text == _ADD_NEW_NAME:
            self._prompt_create_and_assign_supply_set()
        else:
            supply_set_id = self.supply_set_combo.itemData(index)
            if supply_set_id is not None:
                self._prompt_assign_ancestor_supply_set(supply_set_id)
        self.supply_set_combo.blockSignals(True)
        self.supply_set_combo.setCurrentIndex(0)
        self.supply_set_combo.blockSignals(False)

    def _prompt_assign_ancestor_supply_set(self, supply_set_id: str) -> None:
        """CHANGE 2 (explicit request): opens the SAME `CreateSupplySetDialog`
        used by "Add New...", pre-filled from the EXISTING ancestor
        Supply Set (`initial=existing`) and fully LOCKED (`locked=True`
        -- every field but "Set as Primary" read-only/disabled, see
        that class's own docstring) -- accepting it SHARES the exact
        same SupplySetPlacement id as this domain's own Primary/
        Secondary, confirmed as the correct model (not a clone) because
        `PowerDomainPlacement.primary_supply_set`/`secondary_supply_sets`
        already reference a Supply Set purely by id -- two different
        `PowerDomainPlacement`s referencing that SAME id needs no new
        data-model support at all. Deliberately does NOT touch
        `self.new_supply_sets`/`self.new_supply_ports`/
        `self._available_supply_names` -- nothing new is being
        composed, so nothing new needs to be materialized on accept();
        `self._owner_paths`/the shared Supply Set's own `owner_paths`
        are left completely untouched too, which is exactly what keeps
        this mechanism from ever colliding with multi-hierarchy
        replication (see this class's own module docstring)."""
        existing = self._all_supply_sets().get(supply_set_id)
        if existing is None:
            return
        primary_available = self._primary_id is None
        dialog = CreateSupplySetDialog(self._available_supply_names, self, primary_available=primary_available, initial=existing, locked=True)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if dialog.set_as_primary and primary_available:
                self._primary_id = supply_set_id
            else:
                self._secondary_ids.append(supply_set_id)
            self._refresh_supply_set_list()
            self._refresh_supply_set_combo()

    def _prompt_create_and_assign_supply_set(self) -> None:
        """One of the two ways `supply_set_combo` can add a Supply Set
        to this domain (CHANGE 2 -- the other is
        `_prompt_assign_ancestor_supply_set()`, for an EXISTING
        ancestor Supply Set; this one, still reached via the combo's
        own "Add New..." entry, composes a brand-new one exactly as
        before -- Fix 4A + follow-up simplification, explicit request):
        opens CreateSupplySetDialog with its own "Set as Primary"
        checkbox enabled only when this domain has no Primary yet, then
        assigns the result as Primary or Secondary IMMEDIATELY on
        accept -- no separate "Add" step.

        Fix 3C (explicit request): before committing, rejects a name
        already used by some OTHER Power Domain anywhere in the design
        (`self._global_domains`/`self._global_supply_sets` -- a wider scope
        than `_all_supply_sets()` above, which only covers THIS
        dialog's own scoped/newly-created sets) -- same "warn, keep the
        SAME dialog open so the user's other fields aren't lost, retry"
        precedent _prompt_add_power_supply()'s own while-loop already
        establishes for a duplicate Supply Port name."""
        primary_available = self._primary_id is None
        dialog = CreateSupplySetDialog(
            self._available_supply_names, self, primary_available=primary_available, is_internal_supply=self._is_internal_target, target_path=self._owner_path
        )
        while dialog.exec() == QDialog.DialogCode.Accepted:
            if supply_set_name_used_by_another_domain(dialog.name, self._global_domains, self._global_supply_sets, exclude_domain_id=self._editing_domain_id):
                message_box.warning(
                    self,
                    "Duplicate Supply Set Name",
                    f"A Supply Set named '{dialog.name}' is already used by another Power Domain. Choose a different name.",
                )
                continue
            conflict = supply_set_with_duplicate_pin_combination(dialog.power, dialog.ground, dialog.n_well, dialog.p_well, self._this_domains_supply_sets())
            if conflict is not None:
                message_box.warning(
                    self,
                    "Duplicate Supply Set Pin Combination",
                    f"This Power/Ground/N-Well/P-Well combination is already used by Supply Set '{conflict.name}'. Choose a different combination.",
                )
                continue
            supply_set = SupplySetPlacement(
                id=uuid4().hex,
                name=dialog.name,
                power=dialog.power,
                ground=dialog.ground,
                n_well=dialog.n_well,
                p_well=dialog.p_well,
                owner_paths=list(self._owner_paths),
            )
            self.new_supply_sets[supply_set.id] = supply_set
            self.new_supply_ports.update(dialog.new_port_colors)
            self.new_supply_port_states.update(dialog.new_port_states)
            self._available_supply_names = sorted(set(self._available_supply_names) | set(dialog.new_port_colors))
            if dialog.set_as_primary and primary_available:
                self._primary_id = supply_set.id
            else:
                self._secondary_ids.append(supply_set.id)
            self._refresh_supply_set_list()
            self._refresh_supply_set_combo()
            return

    def _refresh_supply_set_list(self) -> None:
        self.supply_set_list.clear()
        all_sets = self._all_supply_sets()
        if self._primary_id is not None and self._primary_id in all_sets:
            item = QListWidgetItem(f"Primary: {all_sets[self._primary_id].name}")
            item.setData(Qt.ItemDataRole.UserRole, ("primary", self._primary_id))
            self.supply_set_list.addItem(item)
        for set_id in self._secondary_ids:
            if set_id in all_sets:
                item = QListWidgetItem(f"Secondary: {all_sets[set_id].name}")
                item.setData(Qt.ItemDataRole.UserRole, ("secondary", set_id))
                self.supply_set_list.addItem(item)

    def _remove_selected_supply_set(self) -> None:
        item = self.supply_set_list.currentItem()
        if item is None:
            return
        role, set_id = item.data(Qt.ItemDataRole.UserRole)
        if role == "primary":
            self._primary_id = None
        elif set_id in self._secondary_ids:
            self._secondary_ids.remove(set_id)
        self._refresh_supply_set_list()
        # CHANGE 2: an ancestor Supply Set just removed from this
        # domain's own list becomes selectable again in the combo.
        self._refresh_supply_set_combo()

    # --- accept validation -----------------------------------------------

    def accept(self) -> None:
        name = self.name
        if not validate_no_spaces(name):
            message_box.warning(self, "Invalid Name", NO_SPACES_ERROR)
            return
        if self._primary_id is None:
            message_box.warning(self, "Missing Primary Supply Set", "A Primary Supply Set is required.")
            return
        for domain_id, existing_name in self._existing_domain_names.items():
            if domain_id != self._editing_domain_id and existing_name == name:
                message_box.warning(self, "Duplicate Power Domain Name", f"A Power Domain named '{name}' already exists. Choose a different name.")
                return
        self._discard_unused_new_entities()
        super().accept()

    def _discard_unused_new_entities(self) -> None:
        """Bug fix (explicit request): a Supply Set created via "Create
        Supply Set..." during this dialog's own session, and then
        abandoned before this outer OK -- either by being removed via
        "Remove Selected" (freeing the Primary slot or dropping it from
        Secondary without ever pruning `new_supply_sets` itself), or by a
        supply name introduced within it being replaced by another
        before that inner CreateSupplySetDialog's own OK (the reported
        repro: create Power "VDD_X", realize it's a mistake, create
        "VDD_Y" instead, complete the Supply Set with VDD_Y) -- must
        never be committed to the real diagram. `new_port_colors`/
        `new_supply_ports` accumulate every name EVER created via
        "Add New..." across the whole dialog session with no pruning of
        their own (see _SupplyNameCombo/CreateSupplySetDialog's own
        docstrings) -- create_power_domain()'s own _add_missing_supply_ports()
        materializes a REAL SupplyPortPlacement for every name it's
        handed with no further filtering, so this is the one place that
        must filter before that happens.

        Prunes `self.new_supply_sets` down to only the ids actually
        referenced as Primary/Secondary at this exact moment, then prunes
        `self.new_supply_ports` down to only the names actually
        referenced (Power/Ground/N-Well/P-Well) by one of those retained
        sets. A name that was only ever used by a since-abandoned Supply
        Set/selection is dropped either way, regardless of *why* it ended
        up unused."""
        referenced_ids = {sid for sid in (self._primary_id, *self._secondary_ids) if sid}
        self.new_supply_sets = {sid: s for sid, s in self.new_supply_sets.items() if sid in referenced_ids}
        referenced_names = {n for s in self.new_supply_sets.values() for n in (s.power, s.ground, s.n_well, s.p_well) if n}
        self.new_supply_ports = {name: color for name, color in self.new_supply_ports.items() if name in referenced_names}
        self.new_supply_port_states = {name: states for name, states in self.new_supply_port_states.items() if name in referenced_names}

    # --- accepted values -------------------------------------------------

    @property
    def name(self) -> str:
        return self.name_edit.text().strip()

    @property
    def color(self) -> QColor:
        """The LIVE-derived preview colour (resolve_domain_color()) --
        purely cosmetic here (the caller's own create_power_domain()/
        edit_power_domain() immediately recomputes and overwrites this
        again via _refresh_domain_derived_colors(), the true source of
        truth), kept as a real property so every pre-existing call site
        that reads `dialog.color` still works unchanged."""
        return resolve_domain_color(self._primary_id, self._all_supply_sets(), self._supply_port_icons, fallback=self._fallback_color)

    @property
    def transparency(self) -> float:
        return DOMAIN_FIXED_TRANSPARENCY

    @property
    def primary_supply_set_id(self) -> Optional[str]:
        return self._primary_id

    @property
    def secondary_supply_set_ids(self) -> list[str]:
        return list(self._secondary_ids)
