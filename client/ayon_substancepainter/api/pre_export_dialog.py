"""
Pre-export dialog for selective material and UDIM export.
"""

from qtpy import QtWidgets, QtCore, QtGui
import logging

log = logging.getLogger(__name__)


class PreExportDialog(QtWidgets.QDialog):
    """Dialog for selecting materials and UDIMs to pre-export."""
    
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


class ConfirmOverwriteDialog(QtWidgets.QDialog):
    """Plain confirmation dialog shown when a previous pre-export already
    wrote textures to this instance's staging directory."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.setWindowTitle("Textures Already Exported")
        self.setMinimumWidth(400)
        
        self._setup_ui()
    
    def _setup_ui(self):
        """Build the dialog UI."""
        layout = QtWidgets.QVBoxLayout()
        
        message = QtWidgets.QLabel(
            "This instance was already pre-exported.\n\n"
            "Exporting again will overwrite the existing texture files. "
            "Continue?"
        )
        message.setWordWrap(True)
        layout.addWidget(message)
        layout.addSpacing(10)
        
        button_layout = QtWidgets.QHBoxLayout()
        
        ok_btn = QtWidgets.QPushButton("Overwrite")
        ok_btn.clicked.connect(self.accept)
        
        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        
        button_layout.addStretch()
        button_layout.addWidget(ok_btn)
        button_layout.addWidget(cancel_btn)
        
        layout.addLayout(button_layout)
        
        self.setLayout(layout)
