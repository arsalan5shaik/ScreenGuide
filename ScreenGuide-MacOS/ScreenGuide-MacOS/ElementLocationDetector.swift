//
//  ElementLocationDetector.swift
//  ScreenGuide-MacOS
//
//  Uses Claude's Computer Use API to identify the screen location of UI elements
//  in screenshots. When a user asks about a visible element (e.g., "click the
//  blue button"), this detects the element's coordinates so the buddy can
//  animate to it and point at it.
//

import AppKit
import Foundation

/// Detects the screen location of UI elements in screenshots using Claude's Computer Use API.
/// The Computer Use tool definition activates Claude's specialized pixel-counting training,
/// which is significantly more accurate than regular vision API coordinate extraction.
///
/// **Aspect ratio matching**: Instead of always resizing to 1024x768 (4:3), we pick the
/// Anthropic-recommended resolution closest to the display's actual aspect ratio. Most
/// Macs are 16:10 → 1280x800. This avoids distorting the image Claude sees, which
/// significantly improves X-axis coordinate accuracy.
class ElementLocationDetector {
    private let apiKey: String
    private let apiURL: URL
    private let model: String
    private let session: URLSession

    /// Anthropic-recommended resolutions for Computer Use, paired with their aspect ratios.
    /// We pick the one closest to the actual display aspect ratio to avoid distortion.
    /// Higher resolutions get downsampled by the API and degrade precision, so these
    /// are intentionally small.
    private static let supportedComputerUseResolutions: [(width: Int, height: Int, aspectRatio: Double)] = [
        (1024, 768,  1024.0 / 768.0),  // 4:3   = 1.333 (legacy displays)
        (1280, 800,  1280.0 / 800.0),  // 16:10  = 1.600 (MacBook Air, MacBook Pro, most Macs)
        (1366, 768,  1366.0 / 768.0)   // ~16:9  = 1.779 (external monitors, ultrawide fallback)
    ]

    init(apiKey: String, model: String = "claude-sonnet-4-6") {
        self.apiKey = apiKey
        self.apiURL = URL(string: "https://api.anthropic.com/v1/messages")!
        self.model = model

        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 15
        config.timeoutIntervalForResource = 20
        config.waitsForConnectivity = false
        config.urlCache = nil
        config.httpCookieStorage = nil
        self.session = URLSession(configuration: config)
    }

