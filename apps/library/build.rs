fn main() {
    println!("cargo:rerun-if-env-changed=APP_VERSION");
    // The page is served from http://127.0.0.1 (a "remote" origin to Tauri), so the update commands it may call
    // are declared here and granted in capabilities/default.json; nothing else is reachable from the page.
    let manifest = tauri_build::AppManifest::new().commands(&["update_status", "update_check", "update_install"]);
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(manifest)).expect("tauri build")
}
