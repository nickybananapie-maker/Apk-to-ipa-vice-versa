"""Tests for the Java → Swift transpiler."""

import pytest
from apk2ipa.translators.java_to_swift import JavaToSwiftTranspiler


@pytest.fixture
def t():
    return JavaToSwiftTranspiler(use_ast=False)  # regex mode for speed


class TestTypeMapping:

    def test_int_to_Int(self, t):
        result = t.transpile("int x = 5;", "Test")
        assert "Int" in result.swift_source

    def test_boolean_to_Bool(self, t):
        result = t.transpile("boolean flag = true;", "Test")
        assert "Bool" in result.swift_source

    def test_string_unchanged(self, t):
        result = t.transpile('String name = "hello";', "Test")
        assert "String" in result.swift_source

    def test_void_method(self, t):
        java = "public void doThing() { }"
        result = t.transpile(java, "Test")
        assert "func doThing" in result.swift_source

    def test_null_to_nil(self, t):
        java = "String x = null;"
        result = t.transpile(java, "Test")
        assert "nil" in result.swift_source

    def test_new_removed(self, t):
        java = "Button b = new Button(context);"
        result = t.transpile(java, "Test")
        assert "Button(" in result.swift_source
        # "new" keyword should be gone
        assert " new " not in result.swift_source

    def test_this_to_self(self, t):
        java = "this.name = value;"
        result = t.transpile(java, "Test")
        assert "self." in result.swift_source

    def test_instanceof_to_is(self, t):
        java = "if (x instanceof String) { }"
        result = t.transpile(java, "Test")
        assert " is " in result.swift_source

    def test_system_out_println_to_print(self, t):
        java = 'System.out.println("hello");'
        result = t.transpile(java, "Test")
        assert "print(" in result.swift_source

    def test_log_d_to_print(self, t):
        java = 'Log.d("TAG", "message");'
        result = t.transpile(java, "Test")
        assert "print(" in result.swift_source

    def test_package_removed(self, t):
        java = "package com.example.app;\npublic class Foo {}"
        result = t.transpile(java, "Foo")
        assert "package" not in result.swift_source

    def test_semicolons_removed(self, t):
        java = "int x = 1;"
        result = t.transpile(java, "Test")
        # Semicolons at end of lines should be removed
        for line in result.swift_source.splitlines():
            line = line.strip()
            if line and not line.startswith("//"):
                assert not line.endswith(";"), f"Semicolon found in: {line!r}"

    def test_imports_added(self, t):
        java = "import android.app.Activity;"
        result = t.transpile(java, "Test")
        assert "import UIKit" in result.swift_source

    def test_activity_to_uiviewcontroller(self, t):
        java = "public class MainActivity extends Activity { }"
        result = t.transpile(java, "MainActivity")
        assert "UIViewController" in result.swift_source

    def test_length_to_count(self, t):
        java = "int n = list.length();"
        result = t.transpile(java, "Test")
        assert ".count" in result.swift_source

    def test_equals_to_double_equals(self, t):
        java = 'if (str.equals("hello")) { }'
        result = t.transpile(java, "Test")
        assert "==" in result.swift_source


class TestLifecycleMapping:

    def test_oncreate_to_viewdidload(self, t):
        java = "protected void onCreate(Bundle savedInstanceState) { }"
        result = t.transpile(java, "Test")
        assert "viewDidLoad" in result.swift_source

    def test_onresume_to_viewdidappear(self, t):
        java = "protected void onResume() { }"
        result = t.transpile(java, "Test")
        assert "viewDidAppear" in result.swift_source

    def test_onpause_to_viewwilldisappear(self, t):
        java = "protected void onPause() { }"
        result = t.transpile(java, "Test")
        assert "viewWillDisappear" in result.swift_source


class TestHeaderGenerated:

    def test_auto_generated_comment(self, t):
        result = t.transpile("class Foo { }", "Foo")
        assert "AUTO-GENERATED" in result.swift_source

    def test_foundation_imported(self, t):
        result = t.transpile("class Foo { }", "Foo")
        assert "import Foundation" in result.swift_source
