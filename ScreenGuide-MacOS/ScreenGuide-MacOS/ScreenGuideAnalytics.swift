//
//  ScreenGuideAnalytics.swift
//  ScreenGuide-MacOS
//
//  Centralized PostHog analytics wrapper. All event names and properties
//  are defined here so instrumentation is consistent and easy to audit.
//

import Foundation
import PostHog

enum ScreenGuideAnalytics {

    // MARK: - Setup

    static func configure() {
        let config = PostHogConfig(
            apiKey: "phc_xcQPygmhTMzzYh8wNW92CCwoXmnzqyChAixh8zgpqC3C",
            host: "https://us.i.posthog.com"
        )
        PostHogSDK.shared.setup(config)
    }

    // MARK: - App Lifecycle

    /// Fired once on every app launch in applicationDidFinishLaunching.
    static func trackAppOpened() {
        let version = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "unknown"
        PostHogSDK.shared.capture("app_opened", properties: [
            "app_version": version
        ])
    }

    // MARK: - Onboarding

    /// User clicked the Start button to begin onboarding for the first time.
    static func trackOnboardingStarted() {
        PostHogSDK.shared.capture("onboarding_started")
    }

    /// User clicked "Watch Onboarding Again" from the panel footer.
    static func trackOnboardingReplayed() {
        PostHogSDK.shared.capture("onboarding_replayed")
    }

    /// The onboarding video finished playing to the end.
    static func trackOnboardingVideoCompleted() {
        PostHogSDK.shared.capture("onboarding_video_completed")
    }

