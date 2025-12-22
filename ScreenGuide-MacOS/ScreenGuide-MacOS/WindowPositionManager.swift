//
//  WindowPositionManager.swift
//  ScreenGuide-MacOS
//
//  Manages positioning the app window on the right edge of the screen
//  and shrinking overlapping windows from other apps via the Accessibility API.
//

import AppKit
import ApplicationServices
import ScreenCaptureKit

enum PermissionRequestPresentationDestination: Equatable {
    case alreadyGranted
    case systemPrompt
    case systemSettings
}

@MainActor
class WindowPositionManager {
    private static var hasAttemptedAccessibilitySystemPromptDuringCurrentLaunch = false
    private static var hasAttemptedScreenRecordingSystemPromptDuringCurrentLaunch = false
    private static let hasPreviouslyConfirmedScreenRecordingPermissionUserDefaultsKey = "com.learningbuddy.hasPreviouslyConfirmedScreenRecordingPermission"

    /// Returns true when the Mac currently has more than one connected display.
    /// Uses AppKit's screen list, which is available without ScreenCaptureKit's
    /// shareable-content permission prompt.
    static func currentMacHasMultipleDisplays() -> Bool {
        NSScreen.screens.count > 1
    }

    // MARK: - Accessibility Permission

    /// Returns true if the app has Accessibility permission.
    static func hasAccessibilityPermission() -> Bool {
        AXIsProcessTrusted()
    }

    /// Presents exactly one permission path per tap: the system prompt on the first
    /// attempt, then System Settings on later attempts after macOS has already shown
    /// its one-time alert.
    @discardableResult
    static func requestAccessibilityPermission() -> PermissionRequestPresentationDestination {
        let presentationDestination = permissionRequestPresentationDestination(
            hasPermissionNow: hasAccessibilityPermission(),
            hasAttemptedSystemPrompt: hasAttemptedAccessibilitySystemPromptDuringCurrentLaunch
        )

        switch presentationDestination {
        case .alreadyGranted:
            return .alreadyGranted
        case .systemPrompt:
            hasAttemptedAccessibilitySystemPromptDuringCurrentLaunch = true
            let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue(): true] as CFDictionary
            _ = AXIsProcessTrustedWithOptions(options)
        case .systemSettings:
            openAccessibilitySettings()
        }

