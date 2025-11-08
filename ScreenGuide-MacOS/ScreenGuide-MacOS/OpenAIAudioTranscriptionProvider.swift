//
//  OpenAIAudioTranscriptionProvider.swift
//  ScreenGuide-MacOS
//
//  AI transcription provider backed by OpenAI's audio transcription API.
//

import AVFoundation
import Foundation

struct OpenAIAudioTranscriptionProviderError: LocalizedError {
    let message: String

    var errorDescription: String? {
        message
    }
}

final class OpenAIAudioTranscriptionProvider: BuddyTranscriptionProvider {
    private let apiKey = AppBundleConfiguration.stringValue(forKey: "OpenAIAPIKey")
    private let modelName = AppBundleConfiguration.stringValue(forKey: "OpenAITranscriptionModel")
        ?? "gpt-4o-transcribe"

    let displayName = "OpenAI"
    let requiresSpeechRecognitionPermission = false

    var isConfigured: Bool {
        apiKey != nil
    }

    var unavailableExplanation: String? {
        guard !isConfigured else { return nil }
        return "OpenAI transcription is not configured. Add OpenAIAPIKey to Info.plist."
    }

    func startStreamingSession(
        keyterms: [String],
        onTranscriptUpdate: @escaping (String) -> Void,
        onFinalTranscriptReady: @escaping (String) -> Void,
        onError: @escaping (Error) -> Void
    ) async throws -> any BuddyStreamingTranscriptionSession {
        guard let apiKey else {
            throw OpenAIAudioTranscriptionProviderError(
                message: unavailableExplanation ?? "OpenAI transcription is not configured."
            )
        }

        return OpenAIAudioTranscriptionSession(
            apiKey: apiKey,
            modelName: modelName,
            keyterms: keyterms,
            onTranscriptUpdate: onTranscriptUpdate,
            onFinalTranscriptReady: onFinalTranscriptReady,
            onError: onError
        )
    }
}

