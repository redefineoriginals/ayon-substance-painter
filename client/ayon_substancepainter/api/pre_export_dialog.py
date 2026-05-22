"""
Pre-export dialog for selective material and UDIM export with strategy selection.
"""

from qtpy import QtWidgets, QtCore, QtGui
import logging

log = logging.getLogger(__name__)


class PreExportDialog(QtWidgets.QDialog):
    """Dialog for selecting materials, UDIMs, and export strategy."""
    
    def __init__(self, texture_sets, udim_tiles=None, parent=None):
        """
        Initialize the pre-export dialog.
        
        Args:
            texture_sets (list): List of texture set names to display
            udim_tiles (list): List of UDIM tile names (e.g., ['1001', '1002'])
            parent: Parent widget
        """
        super().__init__(parent)
        self.texture_sets = texture_sets
        self.udim_tiles = udim_tiles or []
        self.selected_materials = []
        self.selected_udims = []
        self.strategy = "version"  # "version" or "overwrite"
        
        self.setWindowTitle("Pre-Export Textures")
        self.setMinimumWidth(500)
        self.setMinimumHeight(400)
        
        self._setup_ui()
    
    def _setup_ui(self):
        """Build the dialog UI."""
        layout = QtWidgets.QVBoxLayout()
        
        # === Material Selection ===
        material_group = self._create_material_group()
        layout.addWidget(material_group)
        
        # === UDIM Selection ===
        if self.udim_tiles:
            udim_group = self._create_udim_group()
            layout.addWidget(udim_group)
        
        # === Strategy Selection ===
        strategy_group = self._create_strategy_group()
        layout.addWidget(strategy_group)
        
        # === Buttons ===
        button_layout = QtWidgets.QHBoxLayout()
        
        export_btn = QtWidgets.QPushButton("Export")
        export_btn.clicked.connect(self.accept)
        
        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        
        button_layout.addStretch()
        button_layout.addWidget(export_btn)
        button_layout.addWidget(cancel_btn)
        
        layout.addLayout(button_layout)
        
        self.setLayout(layout)
    
    def _create_material_group(self):
        """Create material selection group."""
        group = QtWidgets.QGroupBox("Materials to Export")
        layout = QtWidgets.QVBoxLayout()
        
        # Select All / Deselect All buttons
        button_layout = QtWidgets.QHBoxLayout()
        select_all_btn = QtWidgets.QPushButton("Select All")
        deselect_all_btn = QtWidgets.QPushButton("Deselect All")
        
        button_layout.addWidget(select_all_btn)
        button_layout.addWidget(deselect_all_btn)
        button_layout.addStretch()
        layout.addLayout(button_layout)
        
        # Material checkboxes
        self.material_checkboxes = []
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        
        scroll_widget = QtWidgets.QWidget()
        scroll_layout = QtWidgets.QVBoxLayout()
        
        for material in self.texture_sets:
            checkbox = QtWidgets.QCheckBox(material)
            checkbox.setChecked(True)  # Default: all selected
            self.material_checkboxes.append((material, checkbox))
            scroll_layout.addWidget(checkbox)
        
        scroll_layout.addStretch()
        scroll_widget.setLayout(scroll_layout)
        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll)
        
        # Connect buttons
        def select_all():
            for _, checkbox in self.material_checkboxes:
                checkbox.setChecked(True)
        
        def deselect_all():
            for _, checkbox in self.material_checkboxes:
                checkbox.setChecked(False)
        
        select_all_btn.clicked.connect(select_all)
        deselect_all_btn.clicked.connect(deselect_all)
        
        group.setLayout(layout)
        return group
    
    def _create_udim_group(self):
        """Create UDIM selection group."""
        group = QtWidgets.QGroupBox("UDIMs to Export (Optional)")
        layout = QtWidgets.QVBoxLayout()
        
        info_label = QtWidgets.QLabel(
            "Leave unchecked to export all UDIMs. "
            "Check specific UDIMs to export only those tiles."
        )
        info_label.setStyleSheet("color: gray; font-size: 10px;")
        layout.addWidget(info_label)
        
        # Select All / Deselect All buttons
        button_layout = QtWidgets.QHBoxLayout()
        select_all_btn = QtWidgets.QPushButton("Select All")
        deselect_all_btn = QtWidgets.QPushButton("Deselect All")
        
        button_layout.addWidget(select_all_btn)
        button_layout.addWidget(deselect_all_btn)
        button_layout.addStretch()
        layout.addLayout(button_layout)
        
        # UDIM checkboxes in a grid
        self.udim_checkboxes = []
        grid = QtWidgets.QGridLayout()
        
        for i, udim in enumerate(sorted(self.udim_tiles)):
            checkbox = QtWidgets.QCheckBox(udim)
            checkbox.setChecked(False)  # Default: all unchecked (export all)
            self.udim_checkboxes.append((udim, checkbox))
            grid.addWidget(checkbox, i // 4, i % 4)
        
        layout.addLayout(grid)
        
        # Connect buttons
        def select_all():
            for _, checkbox in self.udim_checkboxes:
                checkbox.setChecked(True)
        
        def deselect_all():
            for _, checkbox in self.udim_checkboxes:
                checkbox.setChecked(False)
        
        select_all_btn.clicked.connect(select_all)
        deselect_all_btn.clicked.connect(deselect_all)
        
        group.setLayout(layout)
        return group
    
    def _create_strategy_group(self):
        """Create export strategy selection group."""
        group = QtWidgets.QGroupBox("Export Strategy")
        layout = QtWidgets.QVBoxLayout()
        
        self.strategy_buttons = {}
        
        # Version Strategy (default)
        version_radio = QtWidgets.QRadioButton(
            "Create New Version (v001, v002, v003, ...)"
        )
        version_radio.setChecked(True)
        version_radio.toggled.connect(lambda: self._on_strategy_changed("version"))
        self.strategy_buttons["version"] = version_radio
        
        version_info = QtWidgets.QLabel(
            "Each export creates a new version directory. Safe, never overwrites."
        )
        version_info.setStyleSheet("color: gray; font-size: 10px; margin-left: 20px;")
        
        layout.addWidget(version_radio)
        layout.addWidget(version_info)
        layout.addSpacing(10)
        
        # Overwrite Strategy
        overwrite_radio = QtWidgets.QRadioButton(
            "Overwrite Current Version (Merge mode)"
        )
        overwrite_radio.toggled.connect(lambda: self._on_strategy_changed("overwrite"))
        self.strategy_buttons["overwrite"] = overwrite_radio
        
        overwrite_info = QtWidgets.QLabel(
            "Overwrites files in current version. Only selected materials/UDIMs are updated."
        )
        overwrite_info.setStyleSheet("color: gray; font-size: 10px; margin-left: 20px;")
        
        layout.addWidget(overwrite_radio)
        layout.addWidget(overwrite_info)
        layout.addSpacing(10)
        
        # Warning
        warning = QtWidgets.QLabel(
            " Overwrite mode will replace files in the current version. "
            "Use caution!"
        )
        warning.setStyleSheet("color: orange; font-size: 10px;")
        warning.setWordWrap(True)
        layout.addWidget(warning)
        
        layout.addStretch()
        
        group.setLayout(layout)
        return group
    
    def _on_strategy_changed(self, strategy):
        """Handle strategy radio button change."""
        self.strategy = strategy
    
    def get_selected_materials(self):
        """Return list of selected material names."""
        selected = []
        for material, checkbox in self.material_checkboxes:
            if checkbox.isChecked():
                selected.append(material)
        return selected
    
    def get_selected_udims(self):
        """Return list of selected UDIM tiles, or empty list for all."""
        if not self.udim_checkboxes:
            return []
        
        selected = []
        for udim, checkbox in self.udim_checkboxes:
            if checkbox.isChecked():
                selected.append(udim)
        return selected  # Empty = export all UDIMs
    
    def get_strategy(self):
        """Return selected strategy: 'version' or 'overwrite'."""
        return self.strategy
    
    def accept(self):
        """Override accept to validate selection."""
        self.selected_materials = self.get_selected_materials()
        self.selected_udims = self.get_selected_udims()
        
        if not self.selected_materials:
            QtWidgets.QMessageBox.warning(
                self,
                "No Materials Selected",
                "Please select at least one material to export."
            )
            return
        
        super().accept()


class ExportStrategyDialog(QtWidgets.QDialog):
    """Simple dialog to confirm overwrite strategy if version already exists."""
    
    def __init__(self, current_version, proposed_version, parent=None):
        """
        Initialize the strategy confirmation dialog.
        
        Args:
            current_version (str): Current version path (e.g., "v001")
            proposed_version (str): Next version that would be created (e.g., "v002")
            parent: Parent widget
        """
        super().__init__(parent)
        self.current_version = current_version
        self.proposed_version = proposed_version
        self.choice = None
        
        self.setWindowTitle("Version Already Exists")
        self.setMinimumWidth(400)
        
        self._setup_ui()
    
    def _setup_ui(self):
        """Build the dialog UI."""
        layout = QtWidgets.QVBoxLayout()
        
        # Message
        message = QtWidgets.QLabel(
            f"Version '{self.current_version}' already exists.\n\n"
            "What would you like to do?"
        )
        layout.addWidget(message)
        layout.addSpacing(10)
        
        # Option 1: Overwrite
        option1 = QtWidgets.QRadioButton(
            f"Overwrite '{self.current_version}' (Merge selected materials/UDIMs)"
        )
        option1.setChecked(True)
        self.choice = "overwrite"
        option1.toggled.connect(lambda: self._set_choice("overwrite") if option1.isChecked() else None)
        
        option1_info = QtWidgets.QLabel(
            "Only selected materials/UDIMs will be updated. Others remain unchanged."
        )
        option1_info.setStyleSheet("color: gray; font-size: 10px; margin-left: 20px;")
        
        layout.addWidget(option1)
        layout.addWidget(option1_info)
        layout.addSpacing(10)
        
        # Option 2: Create new version
        option2 = QtWidgets.QRadioButton(
            f"Create new version '{self.proposed_version}'"
        )
        option2.toggled.connect(lambda: self._set_choice("version") if option2.isChecked() else None)
        
        option2_info = QtWidgets.QLabel(
            "Safe option. Creates a new version without affecting existing files."
        )
        option2_info.setStyleSheet("color: gray; font-size: 10px; margin-left: 20px;")
        
        layout.addWidget(option2)
        layout.addWidget(option2_info)
        layout.addSpacing(20)
        
        # Buttons
        button_layout = QtWidgets.QHBoxLayout()
        
        ok_btn = QtWidgets.QPushButton("Continue")
        ok_btn.clicked.connect(self.accept)
        
        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        
        button_layout.addStretch()
        button_layout.addWidget(ok_btn)
        button_layout.addWidget(cancel_btn)
        
        layout.addLayout(button_layout)
        
        self.setLayout(layout)
    
    def _set_choice(self, choice):
        """Set the user's choice."""
        self.choice = choice
    
    def get_choice(self):
        """Return user's choice: 'overwrite' or 'version'."""
        return self.choi
