#!/usr/bin/env swift
// Copyright 2026 blue_lobster
// SPDX-License-Identifier: Apache-2.0

import Dispatch
import Foundation
import IOKit.hid

// A macOS-only, dependency-free hardware spike for the stock Keychron V3 8K
// ANSI encoder firmware. This is not a choice of Abralia's production host
// language or firmware RGB architecture.

private enum DeviceConstants {
    static let vendorID = 0x3434
    static let productID = 0x0F30
    static let rawUsagePage = 0xFF60
    static let rawUsage = 0x61
    static let reportLength = 32
    static let expectedLEDCount = 87
    static let perKeyEffect = 23
}

private enum KeychronRGBCommand: UInt8 {
    case getProtocolVersion = 0x01
    case getLEDCount = 0x05
    case getLEDIndex = 0x06
    case getPerKeyType = 0x07
    case setPerKeyType = 0x08
    case getPerKeyColor = 0x09
    case setPerKeyColor = 0x0A
}

private enum VIACommand: UInt8 {
    case customSetValue = 0x07
    case customGetValue = 0x08
}

private enum VIARGBMatrixValue: UInt8 {
    case brightness = 0x01
    case effect = 0x02
}

private struct MatrixCoordinate {
    let row: UInt8
    let column: UInt8
}

private let keyCoordinates: [String: MatrixCoordinate] = [
    "W": MatrixCoordinate(row: 2, column: 2),
    "A": MatrixCoordinate(row: 3, column: 1),
    "S": MatrixCoordinate(row: 3, column: 2),
    "D": MatrixCoordinate(row: 3, column: 3),
    "SPACE": MatrixCoordinate(row: 5, column: 6),
]

private struct HSV: CustomStringConvertible {
    let hue: UInt8
    let saturation: UInt8
    let value: UInt8

    var description: String {
        "HSV(\(hue), \(saturation), \(value))"
    }
}

private struct NamedColor {
    let name: String
    let hsv: HSV
}

private let palette = [
    NamedColor(name: "red", hsv: HSV(hue: 0, saturation: 255, value: 255)),
    NamedColor(name: "green", hsv: HSV(hue: 85, saturation: 255, value: 255)),
    NamedColor(name: "blue", hsv: HSV(hue: 170, saturation: 255, value: 255)),
    NamedColor(name: "magenta", hsv: HSV(hue: 213, saturation: 255, value: 255)),
    NamedColor(name: "amber", hsv: HSV(hue: 32, saturation: 255, value: 255)),
]

private enum ExperimentError: Error, CustomStringConvertible {
    case badArgument(String)
    case deviceNotFound
    case ambiguousDevices(Int)
    case hid(String, IOReturn)
    case timeout
    case unexpectedResponse([UInt8])
    case commandRejected(UInt8)
    case unexpectedLEDCount(Int)
    case keyHasNoLED(String)
    case interrupted

    var description: String {
        switch self {
        case .badArgument(let message):
            return message
        case .deviceNotFound:
            return "No Keychron V3 8K Raw HID interface was found. Connect the board by USB and close Keychron Launcher/VIA."
        case .ambiguousDevices(let count):
            return "Found \(count) matching Raw HID interfaces; refusing to guess which device to control."
        case .hid(let operation, let code):
            if code == kIOReturnNotPermitted {
                return "\(operation) was denied by macOS. Run from an app allowed under System Settings > Privacy & Security > Input Monitoring."
            }
            return "\(operation) failed with IOKit status 0x\(String(UInt32(bitPattern: code), radix: 16))."
        case .timeout:
            return "Timed out waiting for the keyboard's Raw HID response. Close Keychron Launcher/VIA and try again."
        case .unexpectedResponse(let bytes):
            return "Received an unexpected Raw HID response: \(hex(bytes))."
        case .commandRejected(let command):
            return "The keyboard rejected Keychron RGB command 0x\(String(command, radix: 16))."
        case .unexpectedLEDCount(let count):
            return "The keyboard reported \(count) LEDs, not the expected \(DeviceConstants.expectedLEDCount); refusing to use the ANSI encoder map."
        case .keyHasNoLED(let key):
            return "The firmware did not report an LED for key \(key)."
        case .interrupted:
            return "Interrupted by the user."
        }
    }
}

private final class InterruptFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var interrupted = false

    func set() {
        lock.lock()
        interrupted = true
        lock.unlock()
    }

    func check() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        return interrupted
    }
}

private final class RawHIDDevice {
    private let manager: IOHIDManager
    private let device: IOHIDDevice
    private let inputBuffer: UnsafeMutablePointer<UInt8>
    private var expectedPrefix: (UInt8, UInt8)?
    private var receivedReport: [UInt8]?

    init() throws {
        manager = IOHIDManagerCreate(kCFAllocatorDefault, IOOptionBits(kIOHIDOptionsTypeNone))

        let matching: [String: Any] = [
            kIOHIDVendorIDKey as String: DeviceConstants.vendorID,
            kIOHIDProductIDKey as String: DeviceConstants.productID,
            kIOHIDPrimaryUsagePageKey as String: DeviceConstants.rawUsagePage,
            kIOHIDPrimaryUsageKey as String: DeviceConstants.rawUsage,
        ]
        IOHIDManagerSetDeviceMatching(manager, matching as CFDictionary)

        let managerStatus = IOHIDManagerOpen(manager, IOOptionBits(kIOHIDOptionsTypeNone))
        guard managerStatus == kIOReturnSuccess else {
            throw ExperimentError.hid("Opening IOHIDManager", managerStatus)
        }

        guard let deviceSet = IOHIDManagerCopyDevices(manager) as? Set<IOHIDDevice> else {
            throw ExperimentError.deviceNotFound
        }
        guard deviceSet.count == 1 else {
            if deviceSet.isEmpty {
                throw ExperimentError.deviceNotFound
            }
            throw ExperimentError.ambiguousDevices(deviceSet.count)
        }
        device = deviceSet.first!

        let deviceStatus = IOHIDDeviceOpen(device, IOOptionBits(kIOHIDOptionsTypeNone))
        guard deviceStatus == kIOReturnSuccess else {
            throw ExperimentError.hid("Opening the Keychron Raw HID interface", deviceStatus)
        }

        inputBuffer = .allocate(capacity: DeviceConstants.reportLength)
        inputBuffer.initialize(repeating: 0, count: DeviceConstants.reportLength)

        IOHIDDeviceScheduleWithRunLoop(device, CFRunLoopGetCurrent(), CFRunLoopMode.defaultMode.rawValue)
        IOHIDDeviceRegisterInputReportCallback(
            device,
            inputBuffer,
            DeviceConstants.reportLength,
            { context, result, _, _, _, report, reportLength in
                guard result == kIOReturnSuccess, let context else { return }
                let owner = Unmanaged<RawHIDDevice>.fromOpaque(context).takeUnretainedValue()
                owner.accept(report: report, length: reportLength)
            },
            Unmanaged.passUnretained(self).toOpaque()
        )
    }

    deinit {
        IOHIDDeviceUnscheduleFromRunLoop(device, CFRunLoopGetCurrent(), CFRunLoopMode.defaultMode.rawValue)
        IOHIDDeviceClose(device, IOOptionBits(kIOHIDOptionsTypeNone))
        IOHIDManagerClose(manager, IOOptionBits(kIOHIDOptionsTypeNone))
        inputBuffer.deinitialize(count: DeviceConstants.reportLength)
        inputBuffer.deallocate()
    }

    private func accept(report: UnsafeMutablePointer<UInt8>, length: CFIndex) {
        guard length >= 2, let expectedPrefix else { return }
        let bytes = Array(UnsafeBufferPointer(start: report, count: Int(length)))
        guard bytes[0] == expectedPrefix.0, bytes[1] == expectedPrefix.1 else { return }
        receivedReport = bytes
    }

    func transact(_ requestBytes: [UInt8], timeoutSeconds: Double = 1.0) throws -> [UInt8] {
        guard requestBytes.count >= 2, requestBytes.count <= DeviceConstants.reportLength else {
            throw ExperimentError.badArgument("Raw HID request must contain 2...\(DeviceConstants.reportLength) bytes.")
        }

        var report = requestBytes
        report.append(contentsOf: repeatElement(0, count: DeviceConstants.reportLength - report.count))
        expectedPrefix = (report[0], report[1])
        receivedReport = nil

        let status = report.withUnsafeBytes { bytes -> IOReturn in
            let pointer = bytes.baseAddress!.assumingMemoryBound(to: UInt8.self)
            return IOHIDDeviceSetReport(
                device,
                kIOHIDReportTypeOutput,
                0,
                pointer,
                report.count
            )
        }
        guard status == kIOReturnSuccess else {
            throw ExperimentError.hid("Sending a Raw HID report", status)
        }

        let deadline = Date().addingTimeInterval(timeoutSeconds)
        while receivedReport == nil, Date() < deadline {
            CFRunLoopRunInMode(CFRunLoopMode.defaultMode, 0.01, true)
        }

        guard let response = receivedReport else {
            throw ExperimentError.timeout
        }
        expectedPrefix = nil
        return response
    }
}

private final class KeychronV3RGB {
    private let hid: RawHIDDevice

    init(hid: RawHIDDevice) {
        self.hid = hid
    }

    private func rgb(_ command: KeychronRGBCommand, payload: [UInt8] = []) throws -> [UInt8] {
        let response = try hid.transact([0xA8, command.rawValue] + payload)
        guard response.count >= 3,
              response[0] == 0xA8,
              response[1] == command.rawValue else {
            throw ExperimentError.unexpectedResponse(response)
        }
        guard response[2] == 0 else {
            throw ExperimentError.commandRejected(command.rawValue)
        }
        return response
    }

    func protocolVersion() throws -> (major: UInt8, minor: UInt8) {
        let response = try rgb(.getProtocolVersion)
        return (response[3], response[4])
    }

    func ledCount() throws -> Int {
        Int(try rgb(.getLEDCount)[3])
    }

    func ledIndex(for coordinate: MatrixCoordinate) throws -> UInt8 {
        let mask = UInt32(1) << UInt32(coordinate.column)
        let payload: [UInt8] = [
            coordinate.row,
            UInt8(mask & 0xFF),
            UInt8((mask >> 8) & 0xFF),
            UInt8((mask >> 16) & 0xFF),
        ]
        let response = try rgb(.getLEDIndex, payload: payload)
        return response[3 + Int(coordinate.column)]
    }

    func perKeyType() throws -> UInt8 {
        try rgb(.getPerKeyType)[3]
    }

    func setPerKeyType(_ type: UInt8) throws {
        _ = try rgb(.setPerKeyType, payload: [type])
    }

    func color(at index: UInt8) throws -> HSV {
        let response = try rgb(.getPerKeyColor, payload: [index, 1])
        return HSV(hue: response[3], saturation: response[4], value: response[5])
    }

    func setColor(_ color: HSV, at index: UInt8) throws {
        _ = try rgb(
            .setPerKeyColor,
            payload: [index, 1, color.hue, color.saturation, color.value]
        )
    }

    private func viaGet(_ value: VIARGBMatrixValue) throws -> UInt8 {
        let response = try hid.transact([
            VIACommand.customGetValue.rawValue,
            0x03,
            value.rawValue,
        ])
        guard response.count >= 4,
              response[0] == VIACommand.customGetValue.rawValue,
              response[1] == 0x03,
              response[2] == value.rawValue else {
            throw ExperimentError.unexpectedResponse(response)
        }
        return response[3]
    }

    private func viaSet(_ value: VIARGBMatrixValue, data: UInt8) throws {
        let response = try hid.transact([
            VIACommand.customSetValue.rawValue,
            0x03,
            value.rawValue,
            data,
        ])
        guard response.count >= 4,
              response[0] == VIACommand.customSetValue.rawValue,
              response[1] == 0x03,
              response[2] == value.rawValue else {
            throw ExperimentError.unexpectedResponse(response)
        }
    }

    func effect() throws -> UInt8 {
        try viaGet(.effect)
    }

    func setEffect(_ effect: UInt8) throws {
        try viaSet(.effect, data: effect)
    }

    func brightness() throws -> UInt8 {
        try viaGet(.brightness)
    }

    func setBrightness(_ brightness: UInt8) throws {
        try viaSet(.brightness, data: brightness)
    }
}

private struct Snapshot {
    let effect: UInt8
    let brightness: UInt8
    let perKeyType: UInt8
    let colors: [UInt8: HSV]
}

private struct Options {
    var probeOnly = false
    var holdSeconds = 2.0
    var offSeconds = 1.0
}

private func parseOptions() throws -> Options {
    var options = Options()
    var arguments = Array(CommandLine.arguments.dropFirst())

    while !arguments.isEmpty {
        let argument = arguments.removeFirst()
        switch argument {
        case "--probe":
            options.probeOnly = true
        case "--hold-seconds":
            guard let value = arguments.first, let seconds = Double(value), seconds >= 0 else {
                throw ExperimentError.badArgument("--hold-seconds requires a non-negative number.")
            }
            options.holdSeconds = seconds
            arguments.removeFirst()
        case "--off-seconds":
            guard let value = arguments.first, let seconds = Double(value), seconds >= 0 else {
                throw ExperimentError.badArgument("--off-seconds requires a non-negative number.")
            }
            options.offSeconds = seconds
            arguments.removeFirst()
        case "--help", "-h":
            printUsage()
            exit(EXIT_SUCCESS)
        default:
            throw ExperimentError.badArgument("Unknown argument: \(argument). Use --help for usage.")
        }
    }
    return options
}

private func printUsage() {
    print("""
    Usage: ./v3_8k_rgb_experiment.swift [options]

      --probe                 Identify the keyboard and read capabilities only.
      --hold-seconds N        Hold each color scene for N seconds (default: 2).
      --off-seconds N         Hold the off tests for N seconds (default: 1).
      -h, --help              Show this help.

    The full experiment targets W, A, S, D, and Space. It writes RAM-only
    lighting state, never sends VIA/Keychron save commands, and restores the
    original effect, brightness, per-key type, and touched colors at the end.
    """)
}

private func hex(_ bytes: [UInt8]) -> String {
    bytes.map { String(format: "%02X", $0) }.joined(separator: " ")
}

private func checkedWait(seconds: Double, interruptFlag: InterruptFlag) throws {
    let deadline = Date().addingTimeInterval(seconds)
    while Date() < deadline {
        if interruptFlag.check() {
            throw ExperimentError.interrupted
        }
        RunLoop.current.run(until: min(deadline, Date().addingTimeInterval(0.05)))
    }
}

private func restore(_ snapshot: Snapshot, using keyboard: KeychronV3RGB) throws {
    // Restore while dark so partially restored colors are not shown.
    try keyboard.setBrightness(0)
    for (index, color) in snapshot.colors.sorted(by: { $0.key < $1.key }) {
        try keyboard.setColor(color, at: index)
    }
    try keyboard.setPerKeyType(snapshot.perKeyType)
    try keyboard.setEffect(snapshot.effect)
    try keyboard.setBrightness(snapshot.brightness)
}

private func run() throws {
    let options = try parseOptions()
    let interruptFlag = InterruptFlag()

    signal(SIGINT, SIG_IGN)
    signal(SIGTERM, SIG_IGN)
    let interruptSource = DispatchSource.makeSignalSource(signal: SIGINT, queue: .global())
    let terminateSource = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .global())
    interruptSource.setEventHandler { interruptFlag.set() }
    terminateSource.setEventHandler { interruptFlag.set() }
    interruptSource.resume()
    terminateSource.resume()
    defer {
        interruptSource.cancel()
        terminateSource.cancel()
    }

    let hid = try RawHIDDevice()
    let keyboard = KeychronV3RGB(hid: hid)
    let version = try keyboard.protocolVersion()
    let ledCount = try keyboard.ledCount()

    print("Connected to Keychron V3 8K (3434:0F30, Raw HID FF60:61).")
    print("Keychron RGB protocol: \(version.major).\(version.minor); LEDs: \(ledCount).")
    guard ledCount == DeviceConstants.expectedLEDCount else {
        throw ExperimentError.unexpectedLEDCount(ledCount)
    }

    var ledByKey: [String: UInt8] = [:]
    for key in ["W", "A", "S", "D", "SPACE"] {
        let index = try keyboard.ledIndex(for: keyCoordinates[key]!)
        guard index != 0xFF else {
            throw ExperimentError.keyHasNoLED(key)
        }
        ledByKey[key] = index
        let coordinate = keyCoordinates[key]!
        print("  \(key): matrix \(coordinate.row),\(coordinate.column) -> LED \(index)")
    }

    let currentEffect = try keyboard.effect()
    let currentBrightness = try keyboard.brightness()
    let currentType = try keyboard.perKeyType()
    print("Current RAM state: effect \(currentEffect), brightness \(currentBrightness), per-key type \(currentType).")

    if options.probeOnly {
        print("Probe complete. No lighting state was changed.")
        return
    }

    var originalColors: [UInt8: HSV] = [:]
    for index in ledByKey.values {
        originalColors[index] = try keyboard.color(at: index)
    }
    let snapshot = Snapshot(
        effect: currentEffect,
        brightness: currentBrightness,
        perKeyType: currentType,
        colors: originalColors
    )

    var needsRestore = true
    defer {
        if needsRestore {
            do {
                try restore(snapshot, using: keyboard)
                print("Restored the original RGB state after early exit.")
            } catch {
                FileHandle.standardError.write(Data("WARNING: automatic RGB restore failed: \(error)\n".utf8))
            }
        }
    }

    // These are all no-EEPROM operations. Do not add VIA custom-save (0x09)
    // or Keychron RGB_SAVE (0x02) to this experiment.
    try keyboard.setPerKeyType(0) // solid per-key renderer
    try keyboard.setEffect(UInt8(DeviceConstants.perKeyEffect))
    try keyboard.setBrightness(255)

    print("\nScene 1: setting five individual keys to different colors.")
    for (offset, key) in ["W", "A", "S", "D", "SPACE"].enumerated() {
        let color = palette[offset]
        print("  \(key) -> \(color.name) \(color.hsv)")
        try keyboard.setColor(color.hsv, at: ledByKey[key]!)
    }
    try checkedWait(seconds: options.holdSeconds, interruptFlag: interruptFlag)

    print("\nScene 2: adjusting each key to a different color.")
    for (offset, key) in ["W", "A", "S", "D", "SPACE"].enumerated() {
        let color = palette[(offset + 2) % palette.count]
        print("  \(key) -> \(color.name) \(color.hsv)")
        try keyboard.setColor(color.hsv, at: ledByKey[key]!)
    }
    try checkedWait(seconds: options.holdSeconds, interruptFlag: interruptFlag)

    print("\nOff test 1: requesting V=0 for only the selected keys.")
    print("The checked stock renderer overwrites per-key V with global brightness, so this may expose the known stock-firmware limitation instead of going dark.")
    for (offset, key) in ["W", "A", "S", "D", "SPACE"].enumerated() {
        let color = palette[(offset + 2) % palette.count].hsv
        try keyboard.setColor(HSV(hue: color.hue, saturation: color.saturation, value: 0), at: ledByKey[key]!)
    }
    try checkedWait(seconds: options.offSeconds, interruptFlag: interruptFlag)

    print("\nOff test 2: setting global brightness to 0 so the keyboard is visibly off.")
    try keyboard.setBrightness(0)
    try checkedWait(seconds: options.offSeconds, interruptFlag: interruptFlag)

    try restore(snapshot, using: keyboard)
    needsRestore = false
    print("Restored the original effect, brightness, per-key type, and touched colors. No state was saved to EEPROM.")
}

do {
    try run()
} catch {
    FileHandle.standardError.write(Data("ERROR: \(error)\n".utf8))
    exit(EXIT_FAILURE)
}
