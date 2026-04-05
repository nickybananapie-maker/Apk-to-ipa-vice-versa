"""
Android API → iOS API mapping tables.

These mappings drive the Java→Swift transpiler.  Each entry is:
  android_class  →  (ios_class, notes)

The notes field is included in the generated Swift file as a comment when
the mapping is imperfect or needs developer attention.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ClassMapping:
    ios_class: str
    import_module: str = "UIKit"       # Swift module to import
    notes: str = ""                    # shown as // TODO comment in output
    needs_review: bool = False         # flag for generated report


# ---------------------------------------------------------------------------
# Android class  →  iOS class
# ---------------------------------------------------------------------------

CLASS_MAP: dict[str, ClassMapping] = {
    # -----------------------------------------------------------------------
    # Activities / View Controllers
    # -----------------------------------------------------------------------
    "Activity":             ClassMapping("UIViewController"),
    "AppCompatActivity":    ClassMapping("UIViewController", notes="Replace AppCompatActivity patterns with UIViewController lifecycle"),
    "FragmentActivity":     ClassMapping("UIViewController"),
    "ComponentActivity":    ClassMapping("UIViewController", notes="Jetpack ComponentActivity → UIViewController"),
    "Fragment":             ClassMapping("UIViewController", notes="Fragments map to child UIViewControllers"),
    "DialogFragment":       ClassMapping("UIViewController", notes="Use UIAlertController or custom presented UIViewController"),
    "BottomSheetDialogFragment": ClassMapping("UIViewController", notes="Use UISheetPresentationController (iOS 15+)"),

    # -----------------------------------------------------------------------
    # Views / UIView hierarchy
    # -----------------------------------------------------------------------
    "View":                 ClassMapping("UIView"),
    "ViewGroup":            ClassMapping("UIView"),
    "LinearLayout":         ClassMapping("UIStackView", notes="Set axis (.vertical/.horizontal) to match android:orientation"),
    "RelativeLayout":       ClassMapping("UIView", notes="Use Auto Layout constraints instead of RelativeLayout rules"),
    "FrameLayout":          ClassMapping("UIView", notes="Use a plain UIView; position children with constraints"),
    "ConstraintLayout":     ClassMapping("UIView", notes="Replicate ConstraintLayout rules with NSLayoutConstraint"),
    "CoordinatorLayout":    ClassMapping("UIView", notes="Use UIScrollView + custom layout"),
    "ScrollView":           ClassMapping("UIScrollView"),
    "HorizontalScrollView": ClassMapping("UIScrollView", notes="Set alwaysBounceHorizontal=true"),
    "NestedScrollView":     ClassMapping("UIScrollView"),

    # -----------------------------------------------------------------------
    # Widgets
    # -----------------------------------------------------------------------
    "TextView":             ClassMapping("UILabel"),
    "EditText":             ClassMapping("UITextField", notes="Use UITextView for multiline"),
    "Button":               ClassMapping("UIButton"),
    "ImageView":            ClassMapping("UIImageView"),
    "ImageButton":          ClassMapping("UIButton", notes="Use UIButton with image configuration"),
    "CheckBox":             ClassMapping("UISwitch",  notes="Or use a custom checkbox UIControl"),
    "RadioButton":          ClassMapping("UIButton",  notes="Manage exclusivity manually or use a custom RadioGroup"),
    "RadioGroup":           ClassMapping("UIStackView", notes="Manage radio selection state manually"),
    "Switch":               ClassMapping("UISwitch"),
    "ToggleButton":         ClassMapping("UIButton", notes="Use UIButton with UIButtonConfiguration to toggle state"),
    "SeekBar":              ClassMapping("UISlider"),
    "ProgressBar":          ClassMapping("UIProgressView", notes="For indeterminate: UIActivityIndicatorView"),
    "RatingBar":            ClassMapping("UIView", notes="No native RatingBar; use a custom star-rating UIControl"),
    "Spinner":              ClassMapping("UIPickerView", notes="Or use UIButton that presents a UIAlertController action sheet"),
    "AutoCompleteTextView": ClassMapping("UITextField", notes="Implement UITextFieldDelegate for autocomplete suggestions"),
    "MultiAutoCompleteTextView": ClassMapping("UITextField", notes="Implement autocomplete suggestions manually"),
    "DatePicker":           ClassMapping("UIDatePicker"),
    "TimePicker":           ClassMapping("UIDatePicker", notes="Set datePickerMode = .time"),
    "CalendarView":         ClassMapping("UIDatePicker", notes="Set datePickerMode = .date and style = .inline (iOS 14+)"),
    "NumberPicker":         ClassMapping("UIPickerView"),
    "WebView":              ClassMapping("WKWebView", import_module="WebKit"),

    # -----------------------------------------------------------------------
    # Lists / Collections
    # -----------------------------------------------------------------------
    "ListView":             ClassMapping("UITableView"),
    "GridView":             ClassMapping("UICollectionView"),
    "RecyclerView":         ClassMapping("UICollectionView", notes="UICollectionView with compositional layout (iOS 13+)"),
    "ViewPager":            ClassMapping("UIPageViewController"),
    "ViewPager2":           ClassMapping("UIPageViewController", notes="Or use UICollectionView with paging"),
    "ExpandableListView":   ClassMapping("UITableView", notes="Use sections or custom disclosure cells"),
    "Adapter":              ClassMapping("UICollectionViewDataSource", notes="Implement UICollectionViewDataSource / UITableViewDataSource"),
    "RecyclerView.Adapter": ClassMapping("UICollectionViewDataSource"),
    "RecyclerView.ViewHolder": ClassMapping("UICollectionViewCell"),
    "ListAdapter":          ClassMapping("UICollectionViewDiffableDataSource", notes="iOS 13+ diffable data source"),
    "DiffUtil":             ClassMapping("NSDiffableDataSourceSnapshot"),

    # -----------------------------------------------------------------------
    # Navigation
    # -----------------------------------------------------------------------
    "Intent":               ClassMapping("UIViewController", notes="Use segues, coordinators, or UINavigationController.push"),
    "NavController":        ClassMapping("UINavigationController", notes="Embed in UINavigationController"),
    "NavHostFragment":      ClassMapping("UINavigationController"),
    "ActionBar":            ClassMapping("UINavigationBar"),
    "Toolbar":              ClassMapping("UINavigationBar", notes="Or use a custom UIToolbar"),
    "BottomNavigationView": ClassMapping("UITabBarController", notes="Use UITabBarController for bottom navigation"),
    "NavigationView":       ClassMapping("UITableViewController", notes="Side menu: use UISplitViewController or a custom drawer"),
    "DrawerLayout":         ClassMapping("UISplitViewController", notes="Or implement a custom side-drawer"),
    "TabLayout":            ClassMapping("UISegmentedControl", notes="Or use UITabBarController"),

    # -----------------------------------------------------------------------
    # Dialogs / Sheets
    # -----------------------------------------------------------------------
    "AlertDialog":          ClassMapping("UIAlertController"),
    "AlertDialog.Builder":  ClassMapping("UIAlertController", notes="Use UIAlertController(title:message:preferredStyle:.alert)"),
    "Dialog":               ClassMapping("UIViewController", notes="Present modally with .formSheet or .alert style"),
    "Toast":                ClassMapping("UIView", notes="No native Toast; use a custom banner view or SPM library"),
    "Snackbar":             ClassMapping("UIView", notes="No native Snackbar; implement a custom notification banner"),

    # -----------------------------------------------------------------------
    # Menus
    # -----------------------------------------------------------------------
    "Menu":                 ClassMapping("UIMenu", import_module="UIKit", notes="Use UIMenu (iOS 14+) or UIAlertController"),
    "MenuItem":             ClassMapping("UIAction", notes="Use UIAction or UIBarButtonItem"),
    "PopupMenu":            ClassMapping("UIMenu", notes="Use UIMenu attached to a UIButton (iOS 14+)"),
    "ContextMenu":          ClassMapping("UIContextMenuConfiguration"),

    # -----------------------------------------------------------------------
    # System / App lifecycle
    # -----------------------------------------------------------------------
    "Application":          ClassMapping("UIApplication"),
    "Context":              ClassMapping("UIApplication", notes="Context has no direct equivalent; access via UIApplication.shared or pass references"),
    "Service":              ClassMapping("NSObject", notes="iOS services are replaced by background tasks / URLSession / Core Bluetooth"),
    "IntentService":        ClassMapping("NSObject", notes="Use URLSession background tasks or BGTaskScheduler"),
    "BroadcastReceiver":    ClassMapping("NSObject", notes="Use NotificationCenter.default to post/observe notifications"),

    # -----------------------------------------------------------------------
    # Async / Threading
    # -----------------------------------------------------------------------
    "Thread":               ClassMapping("Thread", import_module="Foundation"),
    "Runnable":             ClassMapping("DispatchWorkItem", import_module="Foundation"),
    "Handler":              ClassMapping("DispatchQueue", import_module="Foundation", notes="Handler.post{} → DispatchQueue.main.async{}"),
    "Looper":               ClassMapping("RunLoop", import_module="Foundation"),
    "AsyncTask":            ClassMapping("Task", import_module="Foundation",
                                        notes="Replace AsyncTask with Swift async/await or DispatchQueue"),
    "ExecutorService":      ClassMapping("OperationQueue", import_module="Foundation"),
    "Executor":             ClassMapping("DispatchQueue", import_module="Foundation"),
    "CountDownLatch":       ClassMapping("DispatchSemaphore", import_module="Foundation"),
    "LiveData":             ClassMapping("Published", import_module="Combine",
                                        notes="Use @Published + Combine or Swift async streams"),
    "ViewModel":            ClassMapping("ObservableObject", import_module="Combine",
                                        notes="Use @ObservableObject with @Published properties"),
    "MutableLiveData":      ClassMapping("CurrentValueSubject", import_module="Combine"),
    "Flow":                 ClassMapping("AnyPublisher", import_module="Combine",
                                        notes="Kotlin Flow → Combine Publisher"),
    "StateFlow":            ClassMapping("CurrentValueSubject", import_module="Combine"),
    "SharedFlow":           ClassMapping("PassthroughSubject", import_module="Combine"),
    "Coroutine":            ClassMapping("Task", import_module="Foundation",
                                        notes="Kotlin coroutines → Swift async/await"),

    # -----------------------------------------------------------------------
    # Data / Storage
    # -----------------------------------------------------------------------
    "SharedPreferences":    ClassMapping("UserDefaults", import_module="Foundation"),
    "SQLiteDatabase":       ClassMapping("SQLiteDatabase", import_module="Foundation",
                                        notes="Use Core Data, SQLite.swift, or GRDB"),
    "SQLiteOpenHelper":     ClassMapping("NSPersistentContainer", import_module="CoreData",
                                        notes="Use Core Data stack"),
    "Room":                 ClassMapping("NSPersistentContainer", import_module="CoreData",
                                        notes="Room → Core Data or SwiftData (iOS 17+)"),
    "ContentProvider":      ClassMapping("NSObject", notes="Expose data via custom APIs instead"),
    "ContentResolver":      ClassMapping("NSObject", notes="No direct equivalent; use frameworks like Photos, Contacts, etc."),
    "File":                 ClassMapping("URL", import_module="Foundation",
                                        notes="Use FileManager and URL for file operations"),
    "FileInputStream":      ClassMapping("InputStream", import_module="Foundation"),
    "FileOutputStream":     ClassMapping("OutputStream", import_module="Foundation"),

    # -----------------------------------------------------------------------
    # Networking
    # -----------------------------------------------------------------------
    "HttpURLConnection":    ClassMapping("URLSession", import_module="Foundation"),
    "OkHttpClient":         ClassMapping("URLSession", import_module="Foundation",
                                        notes="OkHttp → URLSession with URLSessionConfiguration"),
    "Retrofit":             ClassMapping("URLSession", import_module="Foundation",
                                        notes="Retrofit → URLSession or Alamofire (SPM)"),
    "Volley":               ClassMapping("URLSession", import_module="Foundation"),

    # -----------------------------------------------------------------------
    # Media
    # -----------------------------------------------------------------------
    "MediaPlayer":          ClassMapping("AVPlayer", import_module="AVFoundation"),
    "SoundPool":            ClassMapping("AVAudioPlayer", import_module="AVFoundation"),
    "Camera":               ClassMapping("AVCaptureSession", import_module="AVFoundation"),
    "Camera2":              ClassMapping("AVCaptureSession", import_module="AVFoundation"),
    "CameraX":              ClassMapping("AVCaptureSession", import_module="AVFoundation",
                                        notes="Use AVFoundation or the new DataScannerViewController"),
    "ExoPlayer":            ClassMapping("AVPlayer", import_module="AVFoundation",
                                        notes="ExoPlayer → AVPlayer"),

    # -----------------------------------------------------------------------
    # Notifications
    # -----------------------------------------------------------------------
    "NotificationManager":  ClassMapping("UNUserNotificationCenter",
                                        import_module="UserNotifications"),
    "NotificationCompat":   ClassMapping("UNMutableNotificationContent",
                                        import_module="UserNotifications"),
    "PushNotification":     ClassMapping("UNUserNotificationCenter",
                                        import_module="UserNotifications"),

    # -----------------------------------------------------------------------
    # Location
    # -----------------------------------------------------------------------
    "LocationManager":      ClassMapping("CLLocationManager", import_module="CoreLocation"),
    "FusedLocationClient":  ClassMapping("CLLocationManager", import_module="CoreLocation",
                                        notes="FusedLocationProviderClient → CLLocationManager"),
    "Geocoder":             ClassMapping("CLGeocoder", import_module="CoreLocation"),

    # -----------------------------------------------------------------------
    # Sensor
    # -----------------------------------------------------------------------
    "SensorManager":        ClassMapping("CMMotionManager", import_module="CoreMotion"),
    "Sensor":               ClassMapping("CMMotionManager", import_module="CoreMotion"),

    # -----------------------------------------------------------------------
    # Bluetooth
    # -----------------------------------------------------------------------
    "BluetoothAdapter":     ClassMapping("CBCentralManager", import_module="CoreBluetooth"),
    "BluetoothGatt":        ClassMapping("CBPeripheral", import_module="CoreBluetooth"),
    "BluetoothLeScanner":   ClassMapping("CBCentralManager", import_module="CoreBluetooth"),

    # -----------------------------------------------------------------------
    # Authentication / Security
    # -----------------------------------------------------------------------
    "BiometricPrompt":      ClassMapping("LAContext", import_module="LocalAuthentication"),
    "KeyguardManager":      ClassMapping("LAContext", import_module="LocalAuthentication"),
    "KeyStore":             ClassMapping("SecKeychain", import_module="Security",
                                        notes="Use Keychain via SecItem APIs"),

    # -----------------------------------------------------------------------
    # Utility
    # -----------------------------------------------------------------------
    "Bundle":               ClassMapping("Dictionary", import_module="Foundation"),
    "Uri":                  ClassMapping("URL", import_module="Foundation"),
    "Log":                  ClassMapping("OSLog", import_module="OSLog",
                                        notes="Log.d/e/v → os.log or print"),
    "TextUtils":            ClassMapping("String", import_module="Foundation"),
    "DateFormat":           ClassMapping("DateFormatter", import_module="Foundation"),
    "Calendar":             ClassMapping("Calendar", import_module="Foundation"),
    "SystemClock":          ClassMapping("Date", import_module="Foundation"),
    "Parcelable":           ClassMapping("Codable", import_module="Foundation",
                                        notes="Parcelable → Codable (Encodable+Decodable)"),
    "Serializable":         ClassMapping("Codable", import_module="Foundation"),
    "Bitmap":               ClassMapping("UIImage"),
    "Canvas":               ClassMapping("CGContext", import_module="UIKit",
                                        notes="Android Canvas → Core Graphics CGContext"),
    "Paint":                ClassMapping("UIBezierPath", notes="Or use Core Graphics directly"),
    "Drawable":             ClassMapping("UIImage", notes="Or a custom UIView subclass"),
    "AnimationDrawable":    ClassMapping("UIImageView", notes="Use UIImageView.animationImages"),
    "ObjectAnimator":       ClassMapping("UIViewPropertyAnimator"),
    "ValueAnimator":        ClassMapping("UIViewPropertyAnimator"),
    "Animator":             ClassMapping("UIViewPropertyAnimator"),
    "TransitionManager":    ClassMapping("UIViewPropertyAnimator",
                                        notes="Use UIView.animate or UIViewPropertyAnimator"),
    "ViewCompat":           ClassMapping("UIView"),
    "ActivityCompat":       ClassMapping("UIViewController"),
    "ContextCompat":        ClassMapping("UIApplication"),
}


# ---------------------------------------------------------------------------
# Method-level mappings:  android_class.method  →  (ios_expression, notes)
# ---------------------------------------------------------------------------

METHOD_MAP: dict[str, tuple[str, str]] = {
    # Activity lifecycle
    "Activity.onCreate":          ("viewDidLoad", ""),
    "Activity.onStart":           ("viewWillAppear", ""),
    "Activity.onResume":          ("viewDidAppear", ""),
    "Activity.onPause":           ("viewWillDisappear", ""),
    "Activity.onStop":            ("viewDidDisappear", ""),
    "Activity.onDestroy":         ("deinit", ""),
    "Activity.setContentView":    ("// setContentView removed — build UI in viewDidLoad or a XIB/Storyboard", ""),
    "Activity.startActivity":     ("present / navigationController?.pushViewController", "Replace with present(_:animated:) or push"),
    "Activity.finish":            ("dismiss(animated:) / navigationController?.popViewController", ""),
    "Activity.getIntent":         ("// No direct equivalent — use segue prepare(for:sender:) or Coordinator", ""),
    "Activity.runOnUiThread":     ("DispatchQueue.main.async", ""),

    # Fragment
    "Fragment.onCreateView":      ("loadView / viewDidLoad", ""),
    "Fragment.getActivity":       ("parent / presentingViewController", ""),
    "Fragment.getView":           ("view", ""),

    # SharedPreferences
    "SharedPreferences.getString":    ("UserDefaults.standard.string(forKey:)", ""),
    "SharedPreferences.putString":    ("UserDefaults.standard.set(_:forKey:)", ""),
    "SharedPreferences.getBoolean":   ("UserDefaults.standard.bool(forKey:)", ""),
    "SharedPreferences.putBoolean":   ("UserDefaults.standard.set(_:forKey:)", ""),
    "SharedPreferences.getInt":       ("UserDefaults.standard.integer(forKey:)", ""),
    "SharedPreferences.putInt":       ("UserDefaults.standard.set(_:forKey:)", ""),
    "SharedPreferences.commit":       ("UserDefaults.standard.synchronize()", "synchronize() is usually not needed"),
    "SharedPreferences.apply":        ("// apply() not needed — UserDefaults saves automatically", ""),

    # Log
    "Log.d":   ("print",    ""),
    "Log.e":   ("print",    ""),
    "Log.w":   ("print",    ""),
    "Log.i":   ("print",    ""),
    "Log.v":   ("print",    ""),
    "Log.wtf": ("assertionFailure", ""),

    # Toast
    "Toast.makeText":    ("// TODO: Show a custom banner/HUD view", "No native Toast — use a library or custom UIView"),
    "Toast.show":        ("// TODO: Display toast banner", ""),

    # Handler
    "Handler.post":             ("DispatchQueue.main.async", ""),
    "Handler.postDelayed":      ("DispatchQueue.main.asyncAfter(deadline: .now() + delay)", ""),
    "Handler.removeCallbacks":  ("// TODO: Cancel DispatchWorkItem", ""),

    # Context
    "Context.getSharedPreferences": ("UserDefaults.standard", ""),
    "Context.startActivity":         ("present / push", ""),
    "Context.startService":          ("// TODO: Replace with BGTaskScheduler or URLSession background task", ""),
    "Context.getSystemService":      ("// TODO: Map to the appropriate iOS framework", ""),
    "Context.getString":             ("NSLocalizedString", ""),
    "Context.getColor":              ("UIColor(named:)", ""),
    "Context.getDrawable":           ("UIImage(named:)", ""),

    # TextView / UILabel
    "TextView.setText":     (".text = ", ""),
    "TextView.getText":     (".text", ""),
    "TextView.setTextColor": (".textColor = UIColor", ""),
    "TextView.setTextSize": (".font = UIFont.systemFont(ofSize:)", ""),
    "TextView.setVisibility": ("isHidden = (visibility != View.VISIBLE)", ""),

    # Button / UIButton
    "Button.setOnClickListener": ("addTarget(_:action:for: .touchUpInside)", ""),
    "Button.setText":            (".setTitle(_:for: .normal)", ""),

    # ImageView / UIImageView
    "ImageView.setImageResource": (".image = UIImage(named:)", ""),
    "ImageView.setImageBitmap":   (".image = ", ""),

    # RecyclerView
    "RecyclerView.setAdapter":              (".dataSource = ", ""),
    "RecyclerView.setLayoutManager":        (".collectionViewLayout = UICollectionViewFlowLayout()", ""),
    "RecyclerView.Adapter.notifyDataSetChanged": (".reloadData()", ""),
    "RecyclerView.Adapter.notifyItemInserted":   (".insertItems(at:)", ""),
    "RecyclerView.Adapter.notifyItemRemoved":    (".deleteItems(at:)", ""),

    # Intent
    "Intent.putExtra":       ("// Store in a property or pass via Coordinator", ""),
    "Intent.getStringExtra": ("// Retrieve from passed property", ""),
    "Intent.setAction":      ("// No direct equivalent", ""),

    # AlertDialog
    "AlertDialog.Builder.setTitle":          ("UIAlertController(title:", ""),
    "AlertDialog.Builder.setMessage":        ("message:", ""),
    "AlertDialog.Builder.setPositiveButton": ("addAction(UIAlertAction(title: ..., style: .default))", ""),
    "AlertDialog.Builder.setNegativeButton": ("addAction(UIAlertAction(title: ..., style: .cancel))", ""),
    "AlertDialog.Builder.show":              ("present(alertController, animated: true)", ""),

    # Collections
    "ArrayList":             ("Array",          ""),
    "HashMap":               ("Dictionary",     ""),
    "HashSet":               ("Set",            ""),
    "LinkedList":            ("Array",          ""),
    "TreeMap":               ("Dictionary",     ""),
    "Collections.sort":      (".sorted()",      ""),
    "Collections.reverse":   (".reversed()",    ""),
    "TextUtils.isEmpty":     (".isEmpty",       ""),
    "TextUtils.equals":      ("==",             ""),
}


# ---------------------------------------------------------------------------
# Java type → Swift type
# ---------------------------------------------------------------------------

TYPE_MAP: dict[str, str] = {
    # Primitives
    "int":     "Int",
    "long":    "Int64",
    "short":   "Int16",
    "byte":    "UInt8",
    "float":   "Float",
    "double":  "Double",
    "boolean": "Bool",
    "char":    "Character",
    "void":    "Void",

    # Boxed primitives
    "Integer":   "Int",
    "Long":      "Int64",
    "Short":     "Int16",
    "Byte":      "UInt8",
    "Float":     "Float",
    "Double":    "Double",
    "Boolean":   "Bool",
    "Character": "Character",

    # Core objects
    "String":       "String",
    "CharSequence": "String",
    "Object":       "AnyObject",
    "Number":       "NSNumber",

    # Collections (generic forms handled separately)
    "List":          "Array",
    "ArrayList":     "Array",
    "LinkedList":    "Array",
    "Set":           "Set",
    "HashSet":       "Set",
    "TreeSet":       "Set",
    "Map":           "Dictionary",
    "HashMap":       "Dictionary",
    "TreeMap":       "Dictionary",
    "LinkedHashMap": "Dictionary",

    # Other common types
    "Date":          "Date",
    "Calendar":      "Calendar",
    "Uri":           "URL",
    "Bundle":        "[String: Any]",
    "Intent":        "// Intent",  # marker
    "Context":       "// Context", # marker
    "Throwable":     "Error",
    "Exception":     "Error",
    "RuntimeException": "Error",
    "StringBuilder": "String",     # simplified
    "StringBuffer":  "String",
    "Enum":          "enum",

    # Android-specific
    "Bitmap":    "UIImage",
    "Drawable":  "UIImage",
    "View":      "UIView",
    "Activity":  "UIViewController",
    "Fragment":  "UIViewController",
}


# ---------------------------------------------------------------------------
# Android lifecycle method → iOS lifecycle method
# ---------------------------------------------------------------------------

LIFECYCLE_MAP: dict[str, str] = {
    "onCreate":              "viewDidLoad",
    "onStart":               "viewWillAppear(_ animated: Bool)",
    "onResume":              "viewDidAppear(_ animated: Bool)",
    "onPause":               "viewWillDisappear(_ animated: Bool)",
    "onStop":                "viewDidDisappear(_ animated: Bool)",
    "onDestroy":             "deinit",
    "onSaveInstanceState":   "// TODO: Implement state preservation (NSCoding / Codable)",
    "onRestoreInstanceState": "// TODO: Restore state in viewDidLoad",
    "onCreateOptionsMenu":   "// TODO: Set up UINavigationItem.rightBarButtonItems",
    "onOptionsItemSelected": "// TODO: Handle bar button item taps",
    "onBackPressed":         "// TODO: Handle back navigation (navigationController?.popViewController)",
    "onActivityResult":      "// TODO: Use Coordinator pattern or completion closures",
    "onRequestPermissionsResult": "// TODO: Use async requestWhenInUseAuthorization() etc.",
    "onConfigurationChanged": "viewWillTransition(to:with:)",
    # Fragment lifecycle
    "onCreateView":          "loadView",
    "onViewCreated":         "viewDidLoad",
    "onAttach":              "// onAttach → add child VC with addChild()",
    "onDetach":              "// onDetach → remove child VC",
}
