"""
apk2ipa — APK ↔ IPA translation pipeline.

Converts Android APK packages into iOS Xcode projects (and eventually
compiled IPAs), performing best-effort automated translation of:
  • AndroidManifest.xml  →  Info.plist
  • Java/Kotlin source   →  Swift source
  • Android XML layouts  →  SwiftUI views
  • Android resources    →  iOS asset catalogs
"""

__version__ = "0.1.0"
__author__ = "apk2ipa contributors"
