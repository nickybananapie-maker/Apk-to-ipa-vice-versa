"""Tests for the Android layout → SwiftUI converter."""

import pytest
from apk2ipa.translators.layout_to_swiftui import LayoutConverter


@pytest.fixture
def c():
    return LayoutConverter()


def _swift(xml: str, name: str = "TestView") -> str:
    return LayoutConverter().convert(xml, name)


class TestBasicConversions:

    def test_textview_to_text(self):
        xml = '''<TextView xmlns:android="http://schemas.android.com/apk/res/android"
            android:text="Hello World" android:layout_width="wrap_content"
            android:layout_height="wrap_content" />'''
        swift = _swift(xml)
        assert "Text" in swift
        assert "Hello World" in swift

    def test_button_to_button(self):
        xml = '''<Button xmlns:android="http://schemas.android.com/apk/res/android"
            android:text="Click Me" android:layout_width="wrap_content"
            android:layout_height="wrap_content" />'''
        swift = _swift(xml)
        assert "Button" in swift
        assert "Click Me" in swift

    def test_edittext_to_textfield(self):
        xml = '''<EditText xmlns:android="http://schemas.android.com/apk/res/android"
            android:hint="Enter text" android:layout_width="match_parent"
            android:layout_height="wrap_content" />'''
        swift = _swift(xml)
        assert "TextField" in swift
        assert "Enter text" in swift

    def test_imageview_to_image(self):
        xml = '''<ImageView xmlns:android="http://schemas.android.com/apk/res/android"
            android:src="@drawable/logo" android:layout_width="wrap_content"
            android:layout_height="wrap_content" />'''
        swift = _swift(xml)
        assert "Image" in swift
        assert "logo" in swift

    def test_linearlayout_vertical_to_vstack(self):
        xml = '''<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android"
            android:orientation="vertical" android:layout_width="match_parent"
            android:layout_height="match_parent" />'''
        swift = _swift(xml)
        assert "VStack" in swift

    def test_linearlayout_horizontal_to_hstack(self):
        xml = '''<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android"
            android:orientation="horizontal" android:layout_width="match_parent"
            android:layout_height="match_parent" />'''
        swift = _swift(xml)
        assert "HStack" in swift

    def test_scrollview_to_scrollview(self):
        xml = '''<ScrollView xmlns:android="http://schemas.android.com/apk/res/android"
            android:layout_width="match_parent" android:layout_height="match_parent" />'''
        swift = _swift(xml)
        assert "ScrollView" in swift

    def test_recyclerview_to_list(self):
        xml = '''<androidx.recyclerview.widget.RecyclerView
            xmlns:android="http://schemas.android.com/apk/res/android"
            android:layout_width="match_parent" android:layout_height="match_parent" />'''
        swift = _swift(xml)
        assert "List" in swift

    def test_checkbox_to_toggle(self):
        xml = '''<CheckBox xmlns:android="http://schemas.android.com/apk/res/android"
            android:text="Remember me" android:layout_width="wrap_content"
            android:layout_height="wrap_content" />'''
        swift = _swift(xml)
        assert "Toggle" in swift

    def test_switch_to_toggle(self):
        xml = '''<Switch xmlns:android="http://schemas.android.com/apk/res/android"
            android:layout_width="wrap_content" android:layout_height="wrap_content" />'''
        swift = _swift(xml)
        assert "Toggle" in swift

    def test_seekbar_to_slider(self):
        xml = '''<SeekBar xmlns:android="http://schemas.android.com/apk/res/android"
            android:max="100" android:layout_width="match_parent"
            android:layout_height="wrap_content" />'''
        swift = _swift(xml)
        assert "Slider" in swift


class TestLayoutModifiers:

    def test_match_parent_width(self):
        xml = '''<View xmlns:android="http://schemas.android.com/apk/res/android"
            android:layout_width="match_parent" android:layout_height="wrap_content" />'''
        swift = _swift(xml)
        assert "maxWidth: .infinity" in swift

    def test_fixed_width(self):
        xml = '''<View xmlns:android="http://schemas.android.com/apk/res/android"
            android:layout_width="200dp" android:layout_height="wrap_content" />'''
        swift = _swift(xml)
        assert "width: 200" in swift

    def test_padding(self):
        xml = '''<View xmlns:android="http://schemas.android.com/apk/res/android"
            android:padding="16dp" android:layout_width="wrap_content"
            android:layout_height="wrap_content" />'''
        swift = _swift(xml)
        assert ".padding(16)" in swift

    def test_visibility_gone_hidden(self):
        xml = '''<View xmlns:android="http://schemas.android.com/apk/res/android"
            android:visibility="gone" android:layout_width="wrap_content"
            android:layout_height="wrap_content" />'''
        swift = _swift(xml)
        assert ".hidden()" in swift

    def test_alpha(self):
        xml = '''<View xmlns:android="http://schemas.android.com/apk/res/android"
            android:alpha="0.5" android:layout_width="wrap_content"
            android:layout_height="wrap_content" />'''
        swift = _swift(xml)
        assert ".opacity(0.5)" in swift


class TestStructureGenerated:

    def test_swiftui_import(self):
        xml = '''<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android"
            android:layout_width="match_parent" android:layout_height="match_parent" />'''
        swift = _swift(xml, "MyView")
        assert "import SwiftUI" in swift

    def test_struct_declaration(self):
        swift = _swift(
            '''<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android"
               android:layout_width="match_parent" android:layout_height="match_parent" />''',
            "HomeView"
        )
        assert "struct HomeView: View" in swift

    def test_body_property(self):
        swift = _swift(
            '''<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android"
               android:layout_width="match_parent" android:layout_height="match_parent" />''',
        )
        assert "var body: some View" in swift

    def test_preview_macro(self):
        swift = _swift(
            '''<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android"
               android:layout_width="match_parent" android:layout_height="match_parent" />''',
            "FooView"
        )
        assert "#Preview" in swift

    def test_bad_xml_returns_comment(self):
        swift = LayoutConverter().convert("not xml at all", "BadView")
        assert "// Layout parse error" in swift


class TestColorConversion:

    def test_hex_color_rgb(self):
        from apk2ipa.translators.layout_to_swiftui import LayoutConverter
        color = LayoutConverter._color("#FF0000")
        assert "Color(red:" in color
        assert "1.000" in color  # Red channel

    def test_hex_color_argb(self):
        from apk2ipa.translators.layout_to_swiftui import LayoutConverter
        color = LayoutConverter._color("#80FF0000")
        assert "opacity:" in color

    def test_named_color_resource(self):
        from apk2ipa.translators.layout_to_swiftui import LayoutConverter
        color = LayoutConverter._color("@color/primaryColor")
        assert 'Color("primaryColor")' == color
