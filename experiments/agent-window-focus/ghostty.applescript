-- Copyright 2026 blue_lobster
-- SPDX-License-Identifier: Apache-2.0
-- Native Ghostty metadata/focus only. Never sends terminal input.

on run argv
    set operation to item 1 of argv
    set targetID to item 2 of argv
    set fieldSeparator to character id 9
    set rowSeparator to character id 10
    if application "Ghostty" is not running then
        error "Ghostty is not running. Open it yourself before this test."
    end if
    tell application "Ghostty"
        if operation is "list" then
            set outputLines to ""
            repeat with win in windows
                repeat with tabItem in tabs of win
                    repeat with term in terminals of tabItem
                        set outputLines to outputLines & (id of win as text) & fieldSeparator & ¬
                            (id of tabItem as text) & fieldSeparator & (id of term as text) & rowSeparator
                    end repeat
                end repeat
            end repeat
            return outputLines
        else if operation is "current" then
            set activeText to frontmost as text
            if (count of windows) is 0 then
                return activeText & fieldSeparator & fieldSeparator & fieldSeparator
            end if
            set win to front window
            set tabItem to selected tab of win
            set term to focused terminal of tabItem
            return activeText & fieldSeparator & (id of win as text) & fieldSeparator & ¬
                (id of tabItem as text) & fieldSeparator & (id of term as text)
        else if operation is "focus" then
            set matchingTerms to every terminal whose id is targetID
            if (count of matchingTerms) is not 1 then
                error "Terminal ID is missing or ambiguous. No fallback target will be used."
            end if
            focus (item 1 of matchingTerms)
            activate
            return "dispatched"
        else
            error "Unknown operation."
        end if
    end tell
end run
