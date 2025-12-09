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

private final class OpenAIAudioTranscriptionSession: BuddyStreamingTranscriptionSession {
    let finalTranscriptFallbackDelaySeconds: TimeInterval = 8.0

    private struct TranscriptionResponse: Decodable {
        let text: String
    }

    private static let transcriptionURL = URL(string: "https://api.openai.com/v1/audio/transcriptions")!
    private static let targetSampleRate = 16_000

    private let apiKey: String
    private let modelName: String
    private let keyterms: [String]
    private let onTranscriptUpdate: (String) -> Void
    private let onFinalTranscriptReady: (String) -> Void
    private let onError: (Error) -> Void

    private let stateQueue = DispatchQueue(label: "com.learningbuddy.openai.transcription")
    private let audioPCM16Converter = BuddyPCM16AudioConverter(
        targetSampleRate: Double(targetSampleRate)
    )
    private let urlSession: URLSession

    private var bufferedPCM16AudioData = Data()
    private var hasRequestedFinalTranscript = false
    private var hasDeliveredFinalTranscript = false
    private var isCancelled = false
    private var transcriptionUploadTask: Task<Void, Never>?

    init(
        apiKey: String,
        modelName: String,
        keyterms: [String],
        onTranscriptUpdate: @escaping (String) -> Void,
        onFinalTranscriptReady: @escaping (String) -> Void,
        onError: @escaping (Error) -> Void
    ) {
        self.apiKey = apiKey
        self.modelName = modelName
        self.keyterms = keyterms
        self.onTranscriptUpdate = onTranscriptUpdate
        self.onFinalTranscriptReady = onFinalTranscriptReady
        self.onError = onError

        let urlSessionConfiguration = URLSessionConfiguration.default
        urlSessionConfiguration.timeoutIntervalForRequest = 45
        urlSessionConfiguration.timeoutIntervalForResource = 90
        urlSessionConfiguration.waitsForConnectivity = true
        self.urlSession = URLSession(configuration: urlSessionConfiguration)
    }

