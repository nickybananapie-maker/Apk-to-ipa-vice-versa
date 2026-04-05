"""
Android XML Layout → SwiftUI converter.

Android layouts are XML files in res/layout/.  This module parses them and
generates SwiftUI View structs.

Android layout attributes → SwiftUI modifiers:
  android:layout_width="match_parent"  →  .frame(maxWidth: .infinity)
  android:layout_height="wrap_content" →  (default / implicit)
  android:padding="16dp"               →  .padding(16)
  android:margin*                      →  .padding() on parent or .offset()
  android:text="@string/foo"           →  Text(LocalizedStringKey("foo"))
  android:textSize="16sp"              →  .font(.system(size: 16))
  android:textColor="#FF0000"          →  .foregroundColor(Color(hex: 0xFF0000))
  android:background="#RRGGBB"         →  .background(Color(hex: ...))
  android:visibility="gone"           →  hidden or not included
  android:id="@+id/foo"               →  // @State / @Binding binding named foo
  android:src="@drawable/foo"         →  Image("foo")
  android:hint="..."                  →  placeholder in TextField
  android:onClick="foo"               →  .onTapGesture { foo() }
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional


ANDROID_NS = "http://schemas.android.com/apk/res/android"
TOOLS_NS   = "http://schemas.android.com/tools"


def _a(attr: str) -> str:
    """Return the fully qualified Android namespace attribute name."""
    return f"{{{ANDROID_NS}}}{attr}"


@dataclass
class SwiftUIComponent:
    """Intermediate representation of a SwiftUI view."""
    view_type: str
    modifiers: list[str] = field(default_factory=list)
    children: list["SwiftUIComponent"] = field(default_factory=list)
    content: str = ""          # For Text, TextField, etc.
    binding_name: str = ""     # From android:id
    comment: str = ""          # TODO notes


class LayoutConverter:
    """
    Convert an Android XML layout file to a SwiftUI View struct.

    Usage::

        converter = LayoutConverter()
        swift_code = converter.convert(xml_string, view_name="MainView")
    """

    def convert(self, xml_source: str, view_name: str = "ConvertedView") -> str:
        """
        Parse *xml_source* (Android layout XML) and return a SwiftUI source string.
        """
        try:
            # Register namespaces to avoid mangled tags
            ET.register_namespace("android", ANDROID_NS)
            ET.register_namespace("tools", TOOLS_NS)
            root = ET.fromstring(xml_source)
        except ET.ParseError as e:
            return f"// Layout parse error: {e}\n// Original XML must be text (not binary)\n"

        component = self._translate_element(root)
        return self._render_view_struct(component, view_name)

    # ------------------------------------------------------------------
    # Element translation
    # ------------------------------------------------------------------

    def _translate_element(self, elem: ET.Element) -> SwiftUIComponent:
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag

        # Get android:id for binding name
        android_id = elem.get(_a("id"), "")
        binding_name = self._clean_id(android_id)

        comp = SwiftUIComponent(view_type=tag, binding_name=binding_name)

        # Dispatch to per-widget handler
        # Try simple class name first (e.g. "RecyclerView" from "androidx.recyclerview.widget.RecyclerView")
        simple_tag = tag.split(".")[-1] if "." in tag else tag
        handler = (
            getattr(self, f"_handle_{simple_tag.lower()}", None) or
            getattr(self, f"_handle_{tag.lower().replace('.', '_')}", None)
        )
        if handler:
            handler(elem, comp)
        else:
            self._handle_generic_view(elem, comp, tag)

        # Common layout modifiers (width, height, padding, margin, visibility)
        self._apply_layout_attrs(elem, comp)

        # Recurse into children
        for child in elem:
            child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if child_tag.startswith("{"):
                continue  # namespace declaration node
            comp.children.append(self._translate_element(child))

        return comp

    # ------------------------------------------------------------------
    # Per-widget handlers
    # ------------------------------------------------------------------

    def _handle_linearlayout(self, elem: ET.Element, comp: SwiftUIComponent) -> None:
        orientation = elem.get(_a("orientation"), "vertical")
        if orientation == "horizontal":
            comp.view_type = "HStack"
        else:
            comp.view_type = "VStack"
        spacing = self._dp(elem.get(_a("dividerPadding"), "0"))
        if spacing:
            comp.modifiers.append(f"spacing: {spacing}")

    def _handle_relativelayout(self, elem: ET.Element, comp: SwiftUIComponent) -> None:
        comp.view_type = "ZStack"
        comp.comment = "// RelativeLayout → ZStack (manually add alignment guides)"

    def _handle_framelayout(self, elem: ET.Element, comp: SwiftUIComponent) -> None:
        comp.view_type = "ZStack"
        alignment = elem.get(_a("gravity"), "")
        if alignment:
            comp.modifiers.append(f"alignment: {self._gravity_to_alignment(alignment)}")

    def _handle_constraintlayout(self, elem: ET.Element, comp: SwiftUIComponent) -> None:
        comp.view_type = "ZStack"
        comp.comment = (
            "// ConstraintLayout → ZStack\n"
            "// TODO: Recreate constraints using .frame, .offset, .padding, or GeometryReader"
        )

    def _handle_coordinatorlayout(self, elem: ET.Element, comp: SwiftUIComponent) -> None:
        comp.view_type = "ZStack"
        comp.comment = "// CoordinatorLayout → ZStack"

    def _handle_scrollview(self, elem: ET.Element, comp: SwiftUIComponent) -> None:
        comp.view_type = "ScrollView"

    def _handle_horizontalscrollview(self, elem: ET.Element, comp: SwiftUIComponent) -> None:
        comp.view_type = "ScrollView"
        comp.modifiers.append(".horizontal")

    def _handle_nestedscrollview(self, elem: ET.Element, comp: SwiftUIComponent) -> None:
        comp.view_type = "ScrollView"

    def _handle_textview(self, elem: ET.Element, comp: SwiftUIComponent) -> None:
        text = self._resolve_string(elem.get(_a("text"), ""))
        comp.view_type = "Text"
        comp.content = f'"{text}"' if text else '""'

        text_size = self._sp(elem.get(_a("textSize"), ""))
        if text_size:
            comp.modifiers.append(f".font(.system(size: {text_size}))")

        text_color = self._color(elem.get(_a("textColor"), ""))
        if text_color:
            comp.modifiers.append(f".foregroundColor({text_color})")

        style = elem.get(_a("textStyle"), "")
        if "bold" in style:
            comp.modifiers.append(".bold()")
        if "italic" in style:
            comp.modifiers.append(".italic()")

        gravity = elem.get(_a("gravity"), "")
        if gravity:
            comp.modifiers.append(f".multilineTextAlignment({self._gravity_to_text_alignment(gravity)})")

    def _handle_edittext(self, elem: ET.Element, comp: SwiftUIComponent) -> None:
        hint = self._resolve_string(elem.get(_a("hint"), ""))
        input_type = elem.get(_a("inputType"), "")
        binding = self.binding_name_to_state(comp.binding_name)

        comp.view_type = "TextField"
        comp.content = f'"{hint}", text: ${binding}' if hint else f'"", text: ${binding}'

        if "textPassword" in input_type or "numberPassword" in input_type:
            comp.view_type = "SecureField"

        if "textMultiLine" in input_type or elem.get(_a("maxLines"), "1") != "1":
            comp.view_type = "TextEditor"
            comp.comment = "// TextEditor for multiline; bind to @State var"

    def _handle_button(self, elem: ET.Element, comp: SwiftUIComponent) -> None:
        text = self._resolve_string(elem.get(_a("text"), "Button"))
        on_click = elem.get(_a("onClick"), "")
        action = f"{{ {on_click}() }}" if on_click else "{ /* TODO: action */ }"
        comp.view_type = "Button"
        comp.content = f'{action} {{ Text("{text}") }}'

    def _handle_imageview(self, elem: ET.Element, comp: SwiftUIComponent) -> None:
        src = self._resolve_drawable(elem.get(_a("src"), ""))
        comp.view_type = "Image"
        comp.content = f'"{src}"' if src else '"placeholder"'

        scale = elem.get(_a("scaleType"), "")
        if scale:
            comp.modifiers.append(self._scale_type_to_swiftui(scale))

    def _handle_imagebutton(self, elem: ET.Element, comp: SwiftUIComponent) -> None:
        src = self._resolve_drawable(elem.get(_a("src"), ""))
        on_click = elem.get(_a("onClick"), "")
        action = f"{{ {on_click}() }}" if on_click else "{ /* TODO */ }"
        comp.view_type = "Button"
        comp.content = f'{action} {{ Image("{src}") }}'

    def _handle_checkbox(self, elem: ET.Element, comp: SwiftUIComponent) -> None:
        text = self._resolve_string(elem.get(_a("text"), ""))
        binding = self.binding_name_to_state(comp.binding_name)
        comp.view_type = "Toggle"
        comp.content = f'"{text}", isOn: ${binding}'

    def _handle_switch(self, elem: ET.Element, comp: SwiftUIComponent) -> None:
        text = self._resolve_string(elem.get(_a("text"), ""))
        binding = self.binding_name_to_state(comp.binding_name)
        comp.view_type = "Toggle"
        comp.content = f'"{text}", isOn: ${binding}'

    def _handle_togglebutton(self, elem: ET.Element, comp: SwiftUIComponent) -> None:
        self._handle_switch(elem, comp)

    def _handle_seekbar(self, elem: ET.Element, comp: SwiftUIComponent) -> None:
        min_v = elem.get(_a("min"), "0")
        max_v = elem.get(_a("max"), "100")
        binding = self.binding_name_to_state(comp.binding_name)
        comp.view_type = "Slider"
        comp.content = f"value: ${binding}, in: {min_v}...{max_v}"

    def _handle_progressbar(self, elem: ET.Element, comp: SwiftUIComponent) -> None:
        style = elem.get(_a("style"), "")
        if "horizontal" in style.lower() or "HorizontalProgressBar" in style:
            comp.view_type = "ProgressView"
            binding = self.binding_name_to_state(comp.binding_name)
            comp.content = f"value: {binding}"
        else:
            comp.view_type = "ProgressView"  # indeterminate spinner

    def _handle_recyclerview(self, elem: ET.Element, comp: SwiftUIComponent) -> None:
        comp.view_type = "List"
        comp.comment = (
            "// RecyclerView → List (or LazyVStack/LazyVGrid for custom layouts)\n"
            "// TODO: Replace with ForEach over your data model"
        )
        comp.content = "/* items */ { item in\n        Text(item.description)\n    }"

    def _handle_listview(self, elem: ET.Element, comp: SwiftUIComponent) -> None:
        comp.view_type = "List"
        comp.comment = "// ListView → List"
        comp.content = "/* items */ { item in\n        Text(item.description)\n    }"

    def _handle_gridview(self, elem: ET.Element, comp: SwiftUIComponent) -> None:
        comp.view_type = "LazyVGrid"
        comp.comment = "// GridView → LazyVGrid"
        comp.content = "columns: [GridItem(.adaptive(minimum: 80))]) {\n        /* items */\n    "

    def _handle_viewpager(self, elem: ET.Element, comp: SwiftUIComponent) -> None:
        comp.view_type = "TabView"
        comp.comment = "// ViewPager → TabView with .tabViewStyle(.page)"
        comp.modifiers.append(".tabViewStyle(.page)")

    def _handle_viewpager2(self, elem: ET.Element, comp: SwiftUIComponent) -> None:
        self._handle_viewpager(elem, comp)

    def _handle_spinner(self, elem: ET.Element, comp: SwiftUIComponent) -> None:
        binding = self.binding_name_to_state(comp.binding_name)
        comp.view_type = "Picker"
        comp.comment = "// Spinner → Picker"
        comp.content = f'"Select", selection: ${binding}) {{ /* options */ '

    def _handle_webview(self, elem: ET.Element, comp: SwiftUIComponent) -> None:
        url = elem.get(_a("url"), "")
        comp.view_type = "WebView"
        comp.comment = (
            "// WebView → WKWebView (wrap in UIViewRepresentable)\n"
            "// import WebKit"
        )
        comp.content = f'url: URL(string: "{url}")!'

    def _handle_radiogroup(self, elem: ET.Element, comp: SwiftUIComponent) -> None:
        comp.view_type = "VStack"
        comp.comment = "// RadioGroup → VStack with Picker or custom radio buttons"

    def _handle_radiobutton(self, elem: ET.Element, comp: SwiftUIComponent) -> None:
        text = self._resolve_string(elem.get(_a("text"), ""))
        comp.view_type = "Button"
        comp.content = f'{{ /* TODO: select radio */ }} {{ Text("{text}") }}'

    def _handle_tabhost(self, elem: ET.Element, comp: SwiftUIComponent) -> None:
        comp.view_type = "TabView"
        comp.comment = "// TabHost → TabView"

    def _handle_tablayout(self, elem: ET.Element, comp: SwiftUIComponent) -> None:
        comp.view_type = "Picker"
        comp.comment = "// TabLayout → Picker with .segmented style"
        comp.modifiers.append(".pickerStyle(.segmented)")

    def _handle_navigationview(self, elem: ET.Element, comp: SwiftUIComponent) -> None:
        comp.view_type = "NavigationView"
        comp.comment = "// NavigationView (or NavigationStack in iOS 16+)"

    def _handle_generic_view(self, elem: ET.Element, comp: SwiftUIComponent, tag: str) -> None:
        comp.view_type = "VStack"
        comp.comment = f"// TODO: No SwiftUI equivalent found for <{tag}>"

    # ------------------------------------------------------------------
    # Common layout attribute application
    # ------------------------------------------------------------------

    def _apply_layout_attrs(self, elem: ET.Element, comp: SwiftUIComponent) -> None:
        # Width
        width = elem.get(_a("layout_width"), "")
        if width in ("match_parent", "fill_parent"):
            comp.modifiers.append(".frame(maxWidth: .infinity)")
        elif width not in ("wrap_content", ""):
            dp = self._dp(width)
            if dp:
                comp.modifiers.append(f".frame(width: {dp})")

        # Height
        height = elem.get(_a("layout_height"), "")
        if height in ("match_parent", "fill_parent"):
            comp.modifiers.append(".frame(maxHeight: .infinity)")
        elif height not in ("wrap_content", ""):
            dp = self._dp(height)
            if dp:
                comp.modifiers.append(f".frame(height: {dp})")

        # Padding
        padding = elem.get(_a("padding"), "")
        if padding:
            dp = self._dp(padding)
            if dp:
                comp.modifiers.append(f".padding({dp})")

        pad_h = elem.get(_a("paddingHorizontal"), "")
        pad_v = elem.get(_a("paddingVertical"), "")
        if pad_h:
            comp.modifiers.append(f".padding(.horizontal, {self._dp(pad_h)})")
        if pad_v:
            comp.modifiers.append(f".padding(.vertical, {self._dp(pad_v)})")

        # Margin (approximated as padding on the component in SwiftUI)
        margin = elem.get(_a("layout_margin"), "")
        if margin:
            dp = self._dp(margin)
            if dp:
                comp.modifiers.append(f".padding({dp})  // layout_margin")

        # Background
        bg = elem.get(_a("background"), "")
        if bg:
            color = self._color(bg)
            if color:
                comp.modifiers.append(f".background({color})")

        # Visibility
        visibility = elem.get(_a("visibility"), "")
        if visibility == "gone":
            comp.modifiers.append(".hidden()  // visibility=gone")
        elif visibility == "invisible":
            comp.modifiers.append(".opacity(0)  // visibility=invisible")

        # Alpha
        alpha = elem.get(_a("alpha"), "")
        if alpha:
            comp.modifiers.append(f".opacity({alpha})")

        # On click
        on_click = elem.get(_a("onClick"), "")
        if on_click and comp.view_type not in ("Button",):
            comp.modifiers.append(f".onTapGesture {{ {on_click}() }}")

        # Content description → accessibility
        cd = elem.get(_a("contentDescription"), "")
        if cd:
            desc = self._resolve_string(cd)
            comp.modifiers.append(f'.accessibilityLabel("{desc}")')

        # Enabled
        enabled = elem.get(_a("enabled"), "")
        if enabled == "false":
            comp.modifiers.append(".disabled(true)")

    # ------------------------------------------------------------------
    # Code renderer
    # ------------------------------------------------------------------

    def _render_view_struct(self, root: SwiftUIComponent, name: str) -> str:
        lines: list[str] = [
            "import SwiftUI",
            "",
            f"struct {name}: View {{",
            "    // TODO: Add @State / @ObservedObject / @Binding properties here",
        ]

        # Collect binding names from the tree
        bindings = self._collect_bindings(root)
        for b in sorted(bindings):
            lines.append(f"    @State private var {b}: String = \"\"")

        lines.append("")
        lines.append("    var body: some View {")

        # Render root component
        self._render_component(root, lines, indent=8)

        lines.append("    }")
        lines.append("}")
        lines.append("")
        lines.append(f"#Preview {{ {name}() }}")

        return "\n".join(lines)

    def _render_component(self, comp: SwiftUIComponent, lines: list[str], indent: int) -> None:
        pad = " " * indent

        if comp.comment:
            for line in comp.comment.splitlines():
                lines.append(f"{pad}{line}")

        is_container = comp.view_type in (
            "VStack", "HStack", "ZStack", "ScrollView", "List",
            "LazyVStack", "LazyHStack", "LazyVGrid", "LazyHGrid",
            "NavigationView", "NavigationStack", "TabView", "Group",
            "ForEach",
        )

        if is_container:
            spacing_mods = [m for m in comp.modifiers if m.startswith("spacing:")]
            alignment_mods = [m for m in comp.modifiers if m.startswith("alignment:")]
            other_mods = [m for m in comp.modifiers
                          if not m.startswith("spacing:") and not m.startswith("alignment:")]

            params: list[str] = []
            if alignment_mods:
                params.append(alignment_mods[0])
            if spacing_mods:
                params.append(spacing_mods[0])

            param_str = f"({', '.join(params)})" if params else ""
            lines.append(f"{pad}{comp.view_type}{param_str} {{")

            for child in comp.children:
                self._render_component(child, lines, indent + 4)

            lines.append(f"{pad}}}")

            for mod in other_mods:
                lines.append(f"{pad}{mod}")

        elif comp.view_type in ("Text", "Image"):
            lines.append(f"{pad}{comp.view_type}({comp.content})")
            for mod in comp.modifiers:
                lines.append(f"{pad}{mod}")

        elif comp.view_type in ("Button", "Toggle", "Picker", "Slider"):
            lines.append(f"{pad}{comp.view_type}({comp.content})")
            for mod in comp.modifiers:
                lines.append(f"{pad}{mod}")

        elif comp.view_type in ("TextField", "SecureField", "TextEditor"):
            lines.append(f"{pad}{comp.view_type}({comp.content})")
            for mod in comp.modifiers:
                lines.append(f"{pad}{mod}")

        elif comp.view_type == "ProgressView":
            content = f"({comp.content})" if comp.content else ""
            lines.append(f"{pad}ProgressView{content}")
            for mod in comp.modifiers:
                lines.append(f"{pad}{mod}")

        else:
            lines.append(f"{pad}// {comp.view_type}  TODO: translate")
            lines.append(f"{pad}VStack {{")
            for child in comp.children:
                self._render_component(child, lines, indent + 4)
            lines.append(f"{pad}}}")
            for mod in comp.modifiers:
                lines.append(f"{pad}{mod}")

    # ------------------------------------------------------------------
    # Attribute helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _dp(value: str) -> str:
        """Extract numeric dp value → CGFloat string."""
        if not value:
            return ""
        m = re.match(r"^(\d+(?:\.\d+)?)(?:dp|dip|px|sp)?$", value.strip())
        if m:
            return m.group(1)
        return ""

    @staticmethod
    def _sp(value: str) -> str:
        """Extract sp (font size) value."""
        if not value:
            return ""
        m = re.match(r"^(\d+(?:\.\d+)?)sp$", value.strip())
        if m:
            return m.group(1)
        m2 = re.match(r"^(\d+(?:\.\d+)?)$", value.strip())
        if m2:
            return m2.group(1)
        return ""

    @staticmethod
    def _color(value: str) -> str:
        """Convert #RRGGBB or #AARRGGBB to a SwiftUI Color expression."""
        if not value:
            return ""
        m = re.match(r"^#([0-9A-Fa-f]{6,8})$", value.strip())
        if m:
            hex_val = m.group(1).upper()
            if len(hex_val) == 6:
                r = int(hex_val[0:2], 16) / 255
                g = int(hex_val[2:4], 16) / 255
                b = int(hex_val[4:6], 16) / 255
                return f"Color(red: {r:.3f}, green: {g:.3f}, blue: {b:.3f})"
            elif len(hex_val) == 8:
                a = int(hex_val[0:2], 16) / 255
                r = int(hex_val[2:4], 16) / 255
                g = int(hex_val[4:6], 16) / 255
                b = int(hex_val[6:8], 16) / 255
                return f"Color(red: {r:.3f}, green: {g:.3f}, blue: {b:.3f}, opacity: {a:.3f})"
        # Named resource reference: @color/foo → Color("foo")
        m2 = re.match(r"^@(?:color|android:color)/(\w+)$", value.strip())
        if m2:
            return f'Color("{m2.group(1)}")'
        return ""

    @staticmethod
    def _resolve_string(value: str) -> str:
        """Resolve @string/foo → foo (key for NSLocalizedString)."""
        m = re.match(r"^@string/(\w+)$", value)
        if m:
            return m.group(1)
        return value

    @staticmethod
    def _resolve_drawable(value: str) -> str:
        """Resolve @drawable/foo → foo."""
        m = re.match(r"^@(?:drawable|mipmap)/(\w+)$", value)
        if m:
            return m.group(1)
        return value

    @staticmethod
    def _clean_id(android_id: str) -> str:
        """@+id/myButton → myButton."""
        m = re.match(r"@\+?id/(\w+)", android_id)
        if m:
            return m.group(1)
        return ""

    @staticmethod
    def binding_name_to_state(binding: str) -> str:
        """myButton → myButtonText (state variable name)."""
        return f"{binding}Value" if binding else "value"

    @staticmethod
    def _gravity_to_alignment(gravity: str) -> str:
        mapping = {
            "center":         ".center",
            "center_horizontal": ".center",
            "center_vertical":   ".center",
            "left":           ".leading",
            "start":          ".leading",
            "right":          ".trailing",
            "end":            ".trailing",
            "top":            ".top",
            "bottom":         ".bottom",
        }
        return mapping.get(gravity.lower(), ".center")

    @staticmethod
    def _gravity_to_text_alignment(gravity: str) -> str:
        mapping = {
            "center":            ".center",
            "center_horizontal": ".center",
            "left":              ".leading",
            "start":             ".leading",
            "right":             ".trailing",
            "end":               ".trailing",
        }
        return mapping.get(gravity.lower(), ".leading")

    @staticmethod
    def _scale_type_to_swiftui(scale_type: str) -> str:
        mapping = {
            "fitCenter":    ".resizable().scaledToFit()",
            "centerCrop":   ".resizable().scaledToFill().clipped()",
            "fitXY":        ".resizable()",
            "centerInside": ".resizable().scaledToFit()",
            "center":       "",
        }
        return mapping.get(scale_type, ".resizable().scaledToFit()")

    def _collect_bindings(self, comp: SwiftUIComponent) -> set[str]:
        """Recursively collect all binding names from the component tree."""
        result: set[str] = set()
        if comp.binding_name:
            result.add(self.binding_name_to_state(comp.binding_name))
        for child in comp.children:
            result |= self._collect_bindings(child)
        return result
