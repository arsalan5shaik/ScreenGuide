//
//  AssemblyAIStreamingTranscriptionProvider.swift
//  ScreenGuide-MacOS
//
//  Streaming AI transcription provider backed by AssemblyAI's websocket API.
//

import AVFoundation
import Foundation

struct AssemblyAIStreamingTranscriptionProviderError: LocalizedError {
    let message: String

    var errorDescription: String? {
        message
    }
}

final class AssemblyAIStreamingTranscriptionProvider: BuddyTranscriptionProvider {
    /// URL for the Cloudflare Worker endpoint that returns a short-lived
    /// AssemblyAI streaming token. The real API key never leaves the server.
    private static let tokenProxyURL = "https://your-worker-name.your-subdomain.workers.dev/transcribe-token"

    let displayName = "AssemblyAI"
    let requiresSpeechRecognitionPermission = false

    var isConfigured: Bool { true }
    var unavailableExplanation: String? { nil }

    /// Single long-lived URLSession shared across all streaming sessions.
    /// Creating and invalidating a URLSession per session corrupts the OS
    /// connection pool and causes "Socket is not connected" errors after
    /// a few rapid reconnections to the same host.
    private let sharedWebSocketURLSession = URLSession(configuration: .default)

    func startStreamingSession(
        keyterms: [String],
        onTranscriptUpdate: @escaping (String) -> Void,
        onFinalTranscriptReady: @escaping (String) -> Void,
        onError: @escaping (Error) -> Void
    ) async throws -> any BuddyStreamingTranscriptionSession {
        // Fetch a fresh temporary token from the proxy before each session
        let temporaryToken = try await fetchTemporaryToken()
        print("🎙️ AssemblyAI: fetched temporary token (\(temporaryToken.prefix(20))...)")

        let session = AssemblyAIStreamingTranscriptionSession(
            apiKey: nil,
            temporaryToken: temporaryToken,
            urlSession: sharedWebSocketURLSession,
            keyterms: keyterms,
            onTranscriptUpdate: onTranscriptUpdate,
            onFinalTranscriptReady: onFinalTranscriptReady,
            onError: onError
        )

