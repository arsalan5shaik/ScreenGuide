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

    /// Detects the screen location of a UI element the user is asking about.
    ///
    /// - Parameters:
    ///   - screenshotData: JPEG or PNG screenshot data from ScreenCaptureKit
    ///   - userQuestion: The user's voice transcript (e.g., "How do I add a project?")
    ///   - displayWidthInPoints: The captured display's width in screen points
    ///   - displayHeightInPoints: The captured display's height in screen points
    ///
    /// - Returns: A `CGPoint` in display-local macOS coordinates (bottom-left origin) if an
    ///   element was identified, or `nil` if no element was found or detection failed.
    func detectElementLocation(
        screenshotData: Data,
        userQuestion: String,
        displayWidthInPoints: Int,
        displayHeightInPoints: Int
    ) async -> CGPoint? {
        // Pick the Computer Use resolution that best matches this display's aspect ratio.
        // This avoids stretching the screenshot (e.g., squishing a 16:10 Mac display
        // into 4:3), which would distort the image Claude sees and degrade X-axis accuracy.
        let computerUseResolution = bestComputerUseResolution(
            forDisplayWidth: displayWidthInPoints,
            displayHeight: displayHeightInPoints
        )

        print("🎯 ElementLocationDetector: display is \(displayWidthInPoints)x\(displayHeightInPoints) " +
              "(ratio \(String(format: "%.3f", Double(displayWidthInPoints) / Double(displayHeightInPoints)))), " +
              "using Computer Use resolution \(computerUseResolution.width)x\(computerUseResolution.height)")

        // Resize the screenshot to the chosen Computer Use resolution
        guard let resizedScreenshotData = resizeScreenshotForComputerUse(
            originalImageData: screenshotData,
            targetWidth: computerUseResolution.width,
            targetHeight: computerUseResolution.height
        ) else {
            print("⚠️ ElementLocationDetector: failed to resize screenshot")
            return nil
        }

        // Make the Computer Use API call with the matching resolution declared
        guard let computerUseCoordinate = await callComputerUseAPI(
            resizedScreenshotData: resizedScreenshotData,
            userQuestion: userQuestion,
            declaredDisplayWidth: computerUseResolution.width,
            declaredDisplayHeight: computerUseResolution.height
        ) else {
            return nil
        }

        // Clamp coordinates to the valid range — Claude occasionally returns
        // values slightly outside the declared display dimensions, which would
        // map to off-screen positions after scaling.
        let clampedX = max(0, min(computerUseCoordinate.x, CGFloat(computerUseResolution.width)))
        let clampedY = max(0, min(computerUseCoordinate.y, CGFloat(computerUseResolution.height)))

        // Scale coordinates from the Computer Use resolution back to actual display point dimensions
        let scaledX = (clampedX / CGFloat(computerUseResolution.width)) * CGFloat(displayWidthInPoints)
        let scaledYTopLeftOrigin = (clampedY / CGFloat(computerUseResolution.height)) * CGFloat(displayHeightInPoints)

        // Convert from top-left origin (Computer Use / CoreGraphics) to bottom-left origin (AppKit)
        let scaledYBottomLeftOrigin = CGFloat(displayHeightInPoints) - scaledYTopLeftOrigin

        print("🎯 ElementLocationDetector: mapped (\(Int(clampedX)), \(Int(clampedY))) in " +
              "\(computerUseResolution.width)x\(computerUseResolution.height) → " +
              "(\(Int(scaledX)), \(Int(scaledYBottomLeftOrigin))) in " +
              "\(displayWidthInPoints)x\(displayHeightInPoints) display-local AppKit coords")

        return CGPoint(x: scaledX, y: scaledYBottomLeftOrigin)
    }

    // MARK: - Private Helpers

    /// Picks the Anthropic-recommended Computer Use resolution whose aspect ratio
    /// is closest to the actual display, minimizing image distortion.
    private func bestComputerUseResolution(
        forDisplayWidth displayWidth: Int,
        displayHeight: Int
    ) -> (width: Int, height: Int) {
        let displayAspectRatio = Double(displayWidth) / Double(max(1, displayHeight))

        var bestWidth = 1280
        var bestHeight = 800
        var smallestAspectRatioDifference = Double.greatestFiniteMagnitude

        for resolution in Self.supportedComputerUseResolutions {
            let difference = abs(displayAspectRatio - resolution.aspectRatio)
            if difference < smallestAspectRatioDifference {
                smallestAspectRatioDifference = difference
                bestWidth = resolution.width
                bestHeight = resolution.height
            }
        }

        return (width: bestWidth, height: bestHeight)
    }

    /// Calls the Claude Computer Use API with a resized screenshot and user question.
    /// Returns the raw coordinate from Claude's response in the declared resolution space, or nil.
    private func callComputerUseAPI(
        resizedScreenshotData: Data,
        userQuestion: String,
        declaredDisplayWidth: Int,
        declaredDisplayHeight: Int
    ) async -> CGPoint? {
        var request = URLRequest(url: apiURL)
        request.httpMethod = "POST"
        request.timeoutInterval = 15
        request.setValue(apiKey, forHTTPHeaderField: "x-api-key")
        request.setValue("2023-06-01", forHTTPHeaderField: "anthropic-version")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        // The beta header activates Computer Use capabilities and the specialized
        // pixel-counting training that makes coordinate detection accurate.
        request.setValue("computer-use-2025-11-24", forHTTPHeaderField: "anthropic-beta")

        // Detect image media type (PNG vs JPEG)
        let mediaType = detectImageMediaType(for: resizedScreenshotData)
        let base64Screenshot = resizedScreenshotData.base64EncodedString()

        let userPrompt = """
        The user asked this question while looking at their screen: "\(userQuestion)"

        Look at the screenshot. If there is a specific UI element (button, link, menu item, text field, icon, etc.) that the user should interact with or is asking about, click on that element.

        If the question is purely conceptual (e.g., "what does HTML mean?") and there's no specific element to point to, just respond with text saying "no specific element".
        """

        let body: [String: Any] = [
            "model": model,
            "max_tokens": 256,
            "tools": [
                [
                    "type": "computer_20251124",
                    "name": "computer",
                    "display_width_px": declaredDisplayWidth,
                    "display_height_px": declaredDisplayHeight
                ]
            ],
            "messages": [
                [
                    "role": "user",
                    "content": [
                        [
                            "type": "image",
                            "source": [
                                "type": "base64",
                                "media_type": mediaType,
                                "data": base64Screenshot
                            ]
                        ],
                        [
                            "type": "text",
                            "text": userPrompt
                        ]
                    ]
                ]
            ]
        ]

        do {
            let bodyData = try JSONSerialization.data(withJSONObject: body)
            request.httpBody = bodyData

            let payloadMB = Double(bodyData.count) / 1_048_576.0
            print("🎯 ElementLocationDetector: sending \(String(format: "%.1f", payloadMB))MB request " +
                  "(declared \(declaredDisplayWidth)x\(declaredDisplayHeight))")

