-- Copyright 2026 blue_lobster
-- SPDX-License-Identifier: Apache-2.0
-- Exact uniquely titled VS Code window discovery/focus; never sends input.

on run argv
    set operation to item 1 of argv
    set targetToken to item 2 of argv
    set fieldSeparator to character id 9

    tell application "System Events"
        set codeProcesses to every application process whose bundle identifier is "com.microsoft.VSCode"
        if (count of codeProcesses) is not 1 then
            error "Expected exactly one VS Code application process. No fallback will be used."
        end if
        set codeProcess to item 1 of codeProcesses
        set matchingWindows to every window of codeProcess whose name contains targetToken
        if (count of matchingWindows) is not 1 then
            error "VS Code target window is missing or ambiguous. No fallback will be used."
        end if
        set targetWindow to item 1 of matchingWindows

        if operation is "identify" then
            set isMain to value of attribute "AXMain" of targetWindow
            return (frontmost of codeProcess as text) & fieldSeparator & ¬
                (isMain as text) & fieldSeparator & (name of targetWindow as text)
        else if operation is "focus" then
            set frontmost of codeProcess to true
            perform action "AXRaise" of targetWindow
            return name of targetWindow as text
        else
            error "Unknown operation."
        end if
    end tell
end run
