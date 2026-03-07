-- AVE DominaPlus Wireshark Dissector
-- Place in Wireshark plugins directory or load via -X lua_script:ave_dominaplus.lua
--
-- Protocol framing (over WebSocket text/binary on port 14001):
--   STX(0x02) + command [+ GS(0x1D) + param1 + GS + param2 ...]
--                       [+ RS(0x1E) + rec1_f1 + GS + rec1_f2 ...]
--              + ETX(0x03) + CRC(2 hex chars) + EOT(0x04)

local ave = Proto("ave_domina", "AVE DominaPlus")

-- Protocol fields
local f_raw       = ProtoField.string("ave.raw",       "Raw Message")
local f_command   = ProtoField.string("ave.command",    "Command")
local f_cmd_desc  = ProtoField.string("ave.cmd_desc",   "Description")
local f_params    = ProtoField.string("ave.params",     "Parameters")
local f_param     = ProtoField.string("ave.param",      "Parameter")
local f_records   = ProtoField.string("ave.records",    "Records")
local f_record    = ProtoField.string("ave.record",     "Record")
local f_field     = ProtoField.string("ave.field",      "Field")
local f_crc       = ProtoField.string("ave.crc",        "CRC")
local f_crc_valid = ProtoField.bool  ("ave.crc_valid",  "CRC Valid")

ave.fields = {
    f_raw, f_command, f_cmd_desc, f_params, f_param,
    f_records, f_record, f_field, f_crc, f_crc_valid,
}

-- Control characters
local STX = 0x02
local ETX = 0x03
local EOT = 0x04
local GS  = 0x1D
local RS  = 0x1E

-- Command descriptions
local cmd_names = {
    -- Client -> Server
    ["LM"]   = "List Maps (Areas)",
    ["LDI"]  = "List Devices",
    ["LI2"]  = "List Device Addresses",
    ["LMC"]  = "List Map Commands",
    ["LML"]  = "List Map Labels",
    ["WTS"]  = "Get Thermostat Status",
    ["STS"]  = "Set Thermostat Status",
    ["WSF"]  = "Get Device Status Family",
    ["SU2"]  = "Subscribe Updates 2",
    ["SU3"]  = "Subscribe Updates 3",
    ["GTM"]  = "Get Thermostat Mode",
    ["GMA"]  = "Get Marcia/Arresto",
    ["GNA"]  = "Get No Action",
    ["GGS"]  = "Get Global Security",
    ["GSF"]  = "Get Sensor Family",
    ["PONG"] = "Pong",
    ["PING"] = "Ping",
    ["SIL"]  = "Set Dimmer Level",
    ["EBI"]  = "Light/Energy Command",
    ["EAI"]  = "Shutter Command",
    ["ES"]   = "Execute Scenario",
    ["EBC"]  = "Execute Map Command",
    ["TOO"]  = "Thermostat Local Off",
    ["TUU"]  = "Thermostat Local Off (TS01)",
    ["TTK"]  = "Thermostat Keyboard Lock",
    -- Server -> Client
    ["lm"]   = "List Maps Response",
    ["ldi"]  = "List Devices Response",
    ["li2"]  = "Device Addresses Response",
    ["lmc"]  = "Map Commands Response",
    ["lml"]  = "Map Labels Response",
    ["wts"]  = "Thermostat Status Response",
    ["wsf"]  = "Device Status Family Response",
    ["gsf"]  = "Sensor Family Response",
    ["upd"]  = "Status Update",
    ["ack"]  = "Acknowledgement",
    ["ping"] = "Ping",
    ["net"]  = "Network Status",
}

-- UPD subtypes
local upd_names = {
    ["WS"]  = "Device Status",
    ["WT"]  = "Thermostat",
    ["TP"]  = "Thermostat Set Point",
    ["TM"]  = "Thermostat Mode",
    ["TK"]  = "Thermostat Keyboard Lock",
    ["TW"]  = "Thermostat Window",
    ["TLO"] = "Thermostat Local Off (map)",
    ["TS"]  = "Thermostat Season (map)",
    ["TT"]  = "Thermostat Temperature (map)",
    ["TO"]  = "Thermostat Offset (map)",
    ["TL"]  = "Thermostat Fan Level (map)",
    ["TF"]  = "Thermostat Function",
    ["TR"]  = "Thermostat Request",
    ["UMI"] = "Humidity",
    ["RGB"] = "RGB",
    ["S"]   = "Tutondo",
    ["VI"]  = "Vivaldi",
    ["D"]   = "Device Icon",
    ["A"]   = "Alarm",
    ["X"]   = "Antitheft Area",
    ["GRP"] = "Group Dimmer",
    ["epv"] = "Economizer",
    ["htl"] = "Hotel",
}

-- EBI (light) sub-commands
local ebi_subcmds = {
    ["10"] = "Toggle",
    ["11"] = "On",
    ["12"] = "Off",
    ["2"]  = "Dimmer Step",
    ["3"]  = "Dimmer On",
    ["4"]  = "Dimmer Off",
}

-- EAI (shutter) sub-commands
local eai_subcmds = {
    ["8"] = "Open",
    ["9"] = "Close",
}

-- Thermostat mode values
local thermo_modes = {
    ["0"]  = "Auto (schedule)",
    ["1"]  = "Manual",
    ["31"] = "Antifreeze",
    ["A"]  = "Auto (schedule)",
    ["M"]  = "Manual",
}

-- Thermostat season values
local thermo_seasons = {
    ["0"] = "Summer",
    ["1"] = "Winter",
    ["2"] = "All",
}

-- Thermostat local off values
local thermo_local_off = {
    ["0"] = "ON",
    ["1"] = "OFF (local off)",
}

-- Thermostat keyboard lock values
local thermo_keylock = {
    ["0"] = "Unlocked",
    ["1"] = "Locked",
}

-- Shutter status values
local shutter_statuses = {
    ["1"] = "Open",
    ["2"] = "Opening",
    ["3"] = "Closed",
    ["4"] = "Closing",
    ["5"] = "Stopped",
}

-- Light/dimmer on/off helper
local function light_status(val)
    local n = tonumber(val)
    if not n then return val end
    if n == 0 then return "OFF" end
    return "ON (" .. val .. ")"
end

-- Dimmer level helper
local function dimmer_level(val)
    local n = tonumber(val)
    if not n then return val end
    if n == 0 then return "OFF (0/31)" end
    return "ON level=" .. val .. "/31"
end

-- Format temperature from raw tenths-of-degree value
local function fmt_temp(val)
    local n = tonumber(val)
    if not n then return val end
    return string.format("%.1f°C", n / 10.0)
end

-- Device type names
local device_types = {
    ["1"]  = "Light",
    ["2"]  = "Dimmer",
    ["3"]  = "Shutter",
    ["4"]  = "Thermostat",
    ["5"]  = "Economizer",
    ["6"]  = "Scenario",
    ["9"]  = "Energy",
    ["12"] = "P3000 Area",
    ["13"] = "P3000 Sensor",
    ["14"] = "Audio",
    ["16"] = "Shutter (16)",
    ["17"] = "Abano",
    ["19"] = "Shutter (19)",
    ["22"] = "Light (22)",
}

-- WT (thermostat) sub-type names
local wt_subtypes = {
    ["T"] = "Temperature",
    ["S"] = "Season",
    ["Z"] = "Local Off",
    ["L"] = "Fan Level",
    ["O"] = "Offset",
}

-- Calculate CRC: XOR all bytes, subtract from 0xFF, return 2-char hex
local function calc_crc(data)
    local crc = 0
    for i = 1, #data do
        crc = crc ~ data:byte(i)  -- Lua 5.3+ native bitwise XOR
    end
    crc = 0xFF - crc
    return string.format("%02X", crc)
end

-- Split a string by a single-byte delimiter
local function split(str, delim_byte)
    local parts = {}
    local start = 1
    for i = 1, #str do
        if str:byte(i) == delim_byte then
            parts[#parts + 1] = str:sub(start, i - 1)
            start = i + 1
        end
    end
    parts[#parts + 1] = str:sub(start)
    return parts
end

-- Build a human-readable info string for a parsed message
local function build_info(command, params, records)
    local info = command
    local desc = cmd_names[command]
    if desc then
        info = info .. " (" .. desc .. ")"
    end

    -- Add contextual detail based on command type
    if command == "upd" or command == "UPD" then
        if #params >= 1 then
            local sub = params[1]
            local sub_desc = upd_names[sub] or sub
            info = info .. " " .. sub_desc
            if #params >= 3 then
                info = info .. " dev=" .. params[3]
            end
            if sub == "WS" and #params >= 4 then
                -- Device status: decode based on device type
                local dt = params[2] or ""
                if dt == "3" or dt == "16" or dt == "19" then
                    info = info .. " " .. (shutter_statuses[params[4]] or ("val=" .. params[4]))
                elseif dt == "2" then
                    info = info .. " " .. dimmer_level(params[4])
                elseif dt == "1" or dt == "22" or dt == "9" then
                    info = info .. " " .. light_status(params[4])
                else
                    info = info .. " val=" .. params[4]
                end
            elseif sub == "WT" and #params >= 4 then
                -- WT format: WT, sub_type, device_id, value
                local wt_sub = params[2] or ""
                local wt_name = wt_subtypes[wt_sub] or wt_sub
                if wt_sub == "T" then
                    info = info .. " " .. wt_name .. " " .. fmt_temp(params[4])
                elseif wt_sub == "S" then
                    info = info .. " " .. (thermo_seasons[params[4]] or ("season=" .. params[4]))
                elseif wt_sub == "Z" then
                    info = info .. " " .. (thermo_local_off[params[4]] or ("local_off=" .. params[4]))
                elseif wt_sub == "L" then
                    info = info .. " fan=" .. params[4]
                elseif wt_sub == "O" then
                    info = info .. " offset=" .. fmt_temp(params[4])
                else
                    info = info .. " " .. wt_name .. "=" .. params[4]
                end
            elseif sub == "TP" and #params >= 4 then
                info = info .. " setpoint=" .. fmt_temp(params[4])
            elseif sub == "TM" and #params >= 4 then
                info = info .. " " .. (thermo_modes[params[4]] or ("mode=" .. params[4]))
            elseif sub == "TS" and #params >= 4 then
                info = info .. " " .. (thermo_seasons[params[4]] or ("season=" .. params[4]))
            elseif sub == "TLO" and #params >= 4 then
                info = info .. " " .. (thermo_local_off[params[4]] or ("local_off=" .. params[4]))
            elseif sub == "TT" and #params >= 4 then
                info = info .. " temp=" .. fmt_temp(params[4])
            elseif sub == "TO" and #params >= 4 then
                info = info .. " offset=" .. fmt_temp(params[4])
            elseif sub == "TL" and #params >= 4 then
                info = info .. " fan=" .. params[4]
            elseif sub == "TK" and #params >= 4 then
                info = info .. " " .. (thermo_keylock[params[4]] or ("lock=" .. params[4]))
            elseif sub == "UMI" and #params >= 4 then
                info = info .. " humidity=" .. params[4] .. "%"
            elseif #params >= 4 then
                info = info .. " val=" .. params[4]
            end
        end
    elseif command == "EBI" then
        if #params >= 1 then info = info .. " dev=" .. params[1] end
        if #params >= 2 then
            local sub = ebi_subcmds[params[2]] or params[2]
            info = info .. " " .. sub
        end
    elseif command == "EAI" then
        if #params >= 1 then info = info .. " dev=" .. params[1] end
        if #params >= 2 then
            local sub = eai_subcmds[params[2]] or params[2]
            info = info .. " " .. sub
        end
    elseif command == "STS" then
        if #params >= 1 then info = info .. " dev=" .. params[1] end
        if #records >= 1 and #records[1] >= 3 then
            local season = thermo_seasons[records[1][1]] or records[1][1]
            local mode = thermo_modes[records[1][2]] or records[1][2]
            local sp = tonumber(records[1][3])
            if sp then sp = sp / 10.0 end
            info = info .. " season=" .. season .. " mode=" .. mode
            if sp then info = info .. " sp=" .. string.format("%.1f", sp) .. "°C" end
        end
    elseif command == "SIL" then
        if #params >= 1 then info = info .. " dev=" .. params[1] end
        if #records >= 1 and #records[1] >= 1 then
            info = info .. " level=" .. records[1][1]
        end
    elseif command == "WTS" then
        if #params >= 1 then info = info .. " dev=" .. params[1] end
    elseif command == "wts" then
        if #params >= 1 then info = info .. " dev=" .. params[1] end
        if #records >= 1 and #records[1] >= 10 then
            local r = records[1]
            local temp = fmt_temp(r[6])
            local sp = fmt_temp(r[8])
            local season_bits = tonumber(r[5]) or 0
            local season = thermo_seasons[tostring(season_bits & 0x01)] or "?"
            local mode = thermo_modes[r[7]] or r[7]
            local lo = thermo_local_off[r[10]] or r[10]
            info = info .. " " .. temp .. " sp=" .. sp .. " " .. season .. " " .. mode .. " " .. lo
        end
    elseif command == "WSF" or command == "wsf" then
        if #params >= 1 then
            local dt = device_types[params[1]] or params[1]
            info = info .. " family=" .. dt
        end
    elseif command == "LMC" or command == "lmc" then
        if #params >= 1 then info = info .. " area=" .. params[1] end
    elseif command == "TOO" or command == "TUU" then
        if #params >= 1 then info = info .. " dev=" .. params[1] end
        if #params >= 2 then
            -- TOO/TUU sends current local_off; server inverts it
            -- So sending 0 means "currently ON → turn OFF", sending 1 means "currently OFF → turn ON"
            if params[2] == "0" then
                info = info .. " → Turn OFF"
            elseif params[2] == "1" then
                info = info .. " → Turn ON"
            else
                info = info .. " val=" .. params[2]
            end
        end
    elseif command == "TTK" then
        if #params >= 1 then info = info .. " dev=" .. params[1] end
    elseif command == "ack" then
        if #params >= 1 then info = info .. " " .. params[1] end
    elseif command == "ldi" then
        info = info .. " (" .. #records .. " devices)"
    elseif command == "lm" then
        info = info .. " (" .. #records .. " areas)"
    end

    return info
end

-- Parse a single message (between STX and EOT), add to tree
local function parse_message(tvb, offset, len, tree, pinfo, msg_num)
    local msg_tree = tree:add(ave, tvb(offset, len), "Message " .. msg_num)

    -- Get message bytes as string
    local raw = tvb(offset, len):string()
    msg_tree:add(f_raw, tvb(offset, len), raw)

    -- Strip STX at start
    local body_start = 1
    if raw:byte(1) == STX then
        body_start = 2
    end

    -- Find ETX position
    local etx_pos = nil
    for i = body_start, #raw do
        if raw:byte(i) == ETX then
            etx_pos = i
            break
        end
    end

    if not etx_pos then
        msg_tree:add_expert_info(PI_MALFORMED, PI_ERROR, "Missing ETX")
        return
    end

    -- Payload is between STX and ETX
    local payload = raw:sub(body_start, etx_pos - 1)

    -- CRC is 2 chars after ETX (before EOT)
    local crc_str = ""
    if #raw >= etx_pos + 2 then
        crc_str = raw:sub(etx_pos + 1, etx_pos + 2)
    end

    -- Verify CRC (computed over STX + payload + ETX)
    local crc_data = raw:sub(1, etx_pos)
    local expected_crc = calc_crc(crc_data)
    local crc_ok = (crc_str:upper() == expected_crc:upper())

    local crc_offset = offset + etx_pos  -- byte offset in tvb for CRC
    if #raw >= etx_pos + 2 then
        local crc_item = msg_tree:add(f_crc, tvb(crc_offset, 2), crc_str)
        msg_tree:add(f_crc_valid, tvb(crc_offset, 2), crc_ok)
        if not crc_ok then
            crc_item:add_expert_info(PI_CHECKSUM, PI_WARN,
                "CRC mismatch: got " .. crc_str .. " expected " .. expected_crc)
        end
    end

    -- Split on RS to get command+params and records
    local pieces = split(payload, RS)

    -- First piece: command + GS-separated parameters
    local fields = split(pieces[1], GS)
    local command = fields[1] or ""
    local params = {}
    for i = 2, #fields do
        params[#params + 1] = fields[i]
    end

    -- Parse records
    local records = {}
    for i = 2, #pieces do
        local rec_fields = split(pieces[i], GS)
        records[#records + 1] = rec_fields
    end

    -- Add command to tree
    local cmd_desc = cmd_names[command] or "Unknown"
    -- Find command position in tvb
    local cmd_tvb_offset = offset + body_start - 1
    local cmd_tvb_len = #command
    msg_tree:add(f_command, tvb(cmd_tvb_offset, cmd_tvb_len), command)
    msg_tree:add(f_cmd_desc, tvb(cmd_tvb_offset, cmd_tvb_len), cmd_desc)

    -- Add parameters
    if #params > 0 then
        local params_tree = msg_tree:add(f_params, tvb(offset, len),
            #params .. " parameter(s)")
        for i, p in ipairs(params) do
            local label = "Param " .. i .. ": " .. p

            -- Add contextual labels
            if command == "EBI" then
                if i == 1 then label = "Device ID: " .. p
                elseif i == 2 then label = "Action: " .. (ebi_subcmds[p] or p) end
            elseif command == "EAI" then
                if i == 1 then label = "Device ID: " .. p
                elseif i == 2 then label = "Action: " .. (eai_subcmds[p] or p) end
            elseif command == "STS" or command == "WTS" or command == "wts"
                   or command == "TOO" or command == "TUU" or command == "TTK" then
                if i == 1 then label = "Device ID: " .. p end
                if (command == "TOO" or command == "TUU") and i == 2 then
                    if p == "0" then
                        label = "Current State: ON (server will turn OFF)"
                    elseif p == "1" then
                        label = "Current State: OFF (server will turn ON)"
                    else
                        label = "Local Off: " .. p
                    end
                end
                if command == "TTK" and i == 2 then
                    label = "Lock: " .. (thermo_keylock[p] or p)
                end
            elseif command == "SIL" then
                if i == 1 then label = "Device ID: " .. p end
            elseif command == "WSF" or command == "wsf" then
                if i == 1 then
                    label = "Family: " .. (device_types[p] or p)
                end
            elseif command == "upd" then
                local sub = params[1] or ""
                if i == 1 then label = "Type: " .. (upd_names[p] or p)
                elseif i == 2 then
                    if sub == "WS" then
                        label = "Device Type: " .. (device_types[p] or p)
                    elseif sub == "WT" then
                        label = "Sub-type: " .. (wt_subtypes[p] or p)
                    else
                        label = "Device ID: " .. p
                    end
                elseif i == 3 then
                    if sub == "WS" then label = "Device ID: " .. p
                    elseif sub == "WT" then label = "Device ID: " .. p
                    else label = "Value: " .. p end
                elseif i == 4 then
                    if sub == "WS" then
                        local dt = params[2] or ""
                        if dt == "3" or dt == "16" or dt == "19" then
                            label = "Status: " .. (shutter_statuses[p] or p)
                        elseif dt == "2" then
                            label = "Status: " .. dimmer_level(p)
                        elseif dt == "1" or dt == "22" or dt == "9" then
                            label = "Status: " .. light_status(p)
                        else
                            label = "Status: " .. p
                        end
                    elseif sub == "WT" then
                        local wt_sub = params[2] or ""
                        if wt_sub == "T" then
                            label = "Temperature: " .. fmt_temp(p)
                        elseif wt_sub == "S" then
                            label = "Season: " .. (thermo_seasons[p] or p)
                        elseif wt_sub == "Z" then
                            label = "Local Off: " .. (thermo_local_off[p] or p)
                        elseif wt_sub == "L" then
                            label = "Fan Level: " .. p
                        elseif wt_sub == "O" then
                            label = "Offset: " .. fmt_temp(p)
                        else
                            label = "Value: " .. p
                        end
                    elseif sub == "TP" then
                        label = "Set Point: " .. fmt_temp(p)
                    elseif sub == "TM" then
                        label = "Mode: " .. (thermo_modes[p] or p)
                    elseif sub == "TS" then
                        label = "Season: " .. (thermo_seasons[p] or p)
                    elseif sub == "TLO" then
                        label = "Local Off: " .. (thermo_local_off[p] or p)
                    elseif sub == "TT" then
                        label = "Temperature: " .. fmt_temp(p)
                    elseif sub == "TO" then
                        label = "Offset: " .. fmt_temp(p)
                    elseif sub == "TL" then
                        label = "Fan Level: " .. p
                    elseif sub == "TK" then
                        label = "Keyboard Lock: " .. (thermo_keylock[p] or p)
                    elseif sub == "UMI" then
                        label = "Humidity: " .. p .. "%"
                    else
                        label = "Value: " .. p
                    end
                end
            elseif command == "ack" then
                if i == 1 then label = "Acked Command: " .. p end
            elseif command == "LMC" or command == "lmc" then
                if i == 1 then label = "Area ID: " .. p end
            end

            params_tree:add(f_param, tvb(offset, len), label)
        end
    end

    -- Add records
    if #records > 0 then
        local rec_tree = msg_tree:add(f_records, tvb(offset, len),
            #records .. " record(s)")
        for i, rec in ipairs(records) do
            local rec_label = "Record " .. i
            -- Contextual record labels
            if command == "STS" and #rec >= 3 then
                local season = thermo_seasons[rec[1]] or rec[1]
                local mode = thermo_modes[rec[2]] or rec[2]
                local sp = tonumber(rec[3])
                if sp then sp = string.format("%.1f°C", sp / 10.0) else sp = rec[3] end
                rec_label = "Season=" .. season .. " Mode=" .. mode .. " SP=" .. sp
            elseif command == "SIL" and #rec >= 1 then
                rec_label = "Level: " .. rec[1]
            elseif (command == "ldi") and #rec >= 3 then
                local dt = device_types[rec[3]] or ("type " .. rec[3])
                rec_label = "Dev " .. rec[1] .. ": " .. rec[2] .. " (" .. dt .. ")"
            elseif (command == "lm") and #rec >= 3 then
                rec_label = "Area " .. rec[1] .. ": " .. rec[2] .. " (order " .. rec[3] .. ")"
            elseif (command == "wts") and #rec >= 10 then
                -- WTS response: full thermostat status
                local temp = fmt_temp(rec[6])
                local sp = fmt_temp(rec[8])
                local season_bits = tonumber(rec[5]) or 0
                local season = thermo_seasons[tostring(season_bits & 0x01)] or "?"
                local mode = thermo_modes[rec[7]] or rec[7]
                local lo = thermo_local_off[rec[10]] or rec[10]
                rec_label = temp .. " sp=" .. sp .. " " .. season .. " " .. mode .. " " .. lo
            elseif (command == "wsf") and #rec >= 2 then
                -- WSF response: device status by family
                local dev_id = rec[1]
                local val = rec[2]
                local family = params[1] or ""
                if family == "3" or family == "16" or family == "19" then
                    rec_label = "Dev " .. dev_id .. ": " .. (shutter_statuses[val] or val)
                elseif family == "2" then
                    rec_label = "Dev " .. dev_id .. ": " .. dimmer_level(val)
                elseif family == "1" or family == "22" or family == "9" then
                    rec_label = "Dev " .. dev_id .. ": " .. light_status(val)
                else
                    rec_label = "Dev " .. dev_id .. ": val=" .. val
                end
            elseif (command == "lmc") and #rec >= 3 then
                rec_label = "Cmd " .. rec[1] .. ": " .. rec[2] .. " (type " .. rec[3] .. ")"
            end

            local r_tree = rec_tree:add(f_record, tvb(offset, len), rec_label)

            -- Add decoded field labels
            local field_labels = {}
            if (command == "wts") and #rec >= 10 then
                field_labels = {
                    [1] = "Fan On",
                    [2] = "Fan Level",
                    [3] = "Configuration",
                    [4] = "Offset: " .. fmt_temp(rec[4]),
                    [5] = "Season Bits",
                    [6] = "Temperature: " .. fmt_temp(rec[6]),
                    [7] = "Mode: " .. (thermo_modes[rec[7]] or rec[7]),
                    [8] = "Set Point: " .. fmt_temp(rec[8]),
                    [9] = "Antifreeze",
                    [10] = "Local Off: " .. (thermo_local_off[rec[10]] or rec[10]),
                }
            elseif (command == "wsf") and #rec >= 2 then
                field_labels = {
                    [1] = "Device ID",
                    [2] = "Status",
                }
            elseif (command == "ldi") and #rec >= 3 then
                field_labels = {
                    [1] = "Device ID",
                    [2] = "Name",
                    [3] = "Type: " .. (device_types[rec[3]] or rec[3]),
                }
            end

            for j, fld in ipairs(rec) do
                local fl = field_labels[j]
                if fl then
                    r_tree:add(f_field, tvb(offset, len), fl .. ": " .. fld)
                else
                    r_tree:add(f_field, tvb(offset, len), "Field " .. j .. ": " .. fld)
                end
            end
        end
    end

    -- Build info column
    local info = build_info(command, params, records)
    msg_tree:set_text(info)

    return info
end

-- Main dissector: parse AVE protocol messages (STX..EOT) from payload
function ave.dissector(tvb, pinfo, tree)
    local length = tvb:len()
    if length == 0 then return end

    pinfo.cols.protocol = "AVE DominaPlus"

    local main_tree = tree:add(ave, tvb(), "AVE DominaPlus Protocol")
    local infos = {}

    -- Find message boundaries (STX to EOT)
    local pos = 0
    local msg_num = 0
    while pos < length do
        -- Find STX
        local stx_pos = nil
        for i = pos, length - 1 do
            if tvb(i, 1):uint() == STX then
                stx_pos = i
                break
            end
        end
        if not stx_pos then break end

        -- Find EOT
        local eot_pos = nil
        for i = stx_pos + 1, length - 1 do
            if tvb(i, 1):uint() == EOT then
                eot_pos = i
                break
            end
        end
        if not eot_pos then break end

        -- Parse this message
        msg_num = msg_num + 1
        local msg_len = eot_pos - stx_pos + 1
        local info = parse_message(tvb, stx_pos, msg_len, main_tree, pinfo, msg_num)
        if info then
            infos[#infos + 1] = info
        end

        pos = eot_pos + 1
    end

    if #infos > 0 then
        pinfo.cols.info = table.concat(infos, " | ")
    end
end

-- ============================================================
-- WebSocket frame parser for raw TCP captures on port 14001
-- ============================================================
-- Unmask a WebSocket payload using a 4-byte masking key
local function ws_unmask(payload, mask_key)
    local unmasked = {}
    for i = 1, #payload do
        local mi = ((i - 1) % 4)
        unmasked[i] = string.char(bit32.bxor(payload:byte(i), mask_key:byte(mi + 1)))
    end
    return table.concat(unmasked)
end

-- Parse WebSocket frames from a TVB range starting at `offset`.
-- Returns a list of {payload_str=..., frame_offset=..., frame_len=...}
local function parse_ws_frames(tvb, offset, length)
    local frames = {}
    local pos = offset
    while pos < length do
        -- Need at least 2 bytes for frame header
        if pos + 2 > length then break end

        local b0 = tvb(pos, 1):uint()
        local b1 = tvb(pos + 1, 1):uint()
        local opcode = bit32.band(b0, 0x0F)
        local masked = bit32.band(b1, 0x80) ~= 0
        local payload_len = bit32.band(b1, 0x7F)
        local header_len = 2

        if payload_len == 126 then
            if pos + 4 > length then break end
            payload_len = tvb(pos + 2, 2):uint()
            header_len = 4
        elseif payload_len == 127 then
            if pos + 10 > length then break end
            -- Use only the lower 32 bits (Lua number precision)
            payload_len = tvb(pos + 6, 4):uint()
            header_len = 10
        end

        local mask_key_str = nil
        if masked then
            if pos + header_len + 4 > length then break end
            mask_key_str = tvb(pos + header_len, 4):raw()
            header_len = header_len + 4
        end

        local frame_len = header_len + payload_len
        if pos + frame_len > length then break end

        -- Only process data frames (text=1, binary=2)
        if opcode == 1 or opcode == 2 then
            local raw_payload = tvb(pos + header_len, payload_len):raw()
            local payload_str
            if masked and mask_key_str then
                payload_str = ws_unmask(raw_payload, mask_key_str)
            else
                payload_str = raw_payload
            end
            frames[#frames + 1] = {
                payload_str = payload_str,
                frame_offset = pos,
                frame_len = frame_len,
                masked = masked,
            }
        end

        pos = pos + frame_len
    end
    return frames, pos
end

-- TCP dissector for port 14001: handles WebSocket framing, then AVE protocol
local function ave_tcp_dissector(tvb, pinfo, tree)
    local length = tvb:len()
    if length == 0 then return end

    -- Skip HTTP handshake packets
    local first_bytes = tvb(0, math.min(4, length)):string()
    if first_bytes == "GET " or first_bytes == "HTTP" then
        return
    end

    -- Check if this looks like raw AVE protocol (starts with STX)
    if tvb(0, 1):uint() == STX then
        ave.dissector(tvb, pinfo, tree)
        return
    end

    -- Try to parse as WebSocket frames
    local b0 = tvb(0, 1):uint()
    local opcode = bit32.band(b0, 0x0F)
    -- Valid WS opcodes: 0-2 (data), 8-10 (control)
    if opcode > 2 and opcode < 8 then return end
    if opcode > 10 then return end

    local frames, _ = parse_ws_frames(tvb, 0, length)
    if #frames == 0 then return end

    pinfo.cols.protocol = "AVE DominaPlus"
    local main_tree = tree:add(ave, tvb(), "AVE DominaPlus Protocol")
    local infos = {}
    local msg_num = 0

    for _, frame in ipairs(frames) do
        local payload = frame.payload_str

        -- Find AVE messages (STX..EOT) within the unmasked payload
        local fpos = 1
        while fpos <= #payload do
            -- Find STX
            local stx = nil
            for j = fpos, #payload do
                if payload:byte(j) == STX then
                    stx = j
                    break
                end
            end
            if not stx then break end

            -- Find EOT
            local eot = nil
            for j = stx + 1, #payload do
                if payload:byte(j) == EOT then
                    eot = j
                    break
                end
            end
            if not eot then break end

            -- Create a ByteArray from the unmasked message and wrap in TVB
            local msg_str = payload:sub(stx, eot)
            local ba = ByteArray.new()
            ba:set_size(#msg_str)
            for k = 1, #msg_str do
                ba:set_index(k - 1, msg_str:byte(k))
            end
            local msg_tvb = ba:tvb("AVE Message")

            msg_num = msg_num + 1
            local info = parse_message(msg_tvb, 0, #msg_str, main_tree, pinfo, msg_num)
            if info then
                infos[#infos + 1] = info
            end

            fpos = eot + 1
        end
    end

    if #infos > 0 then
        pinfo.cols.info = table.concat(infos, " | ")
    end
end

-- ============================================================
-- Registration
-- ============================================================

-- Register for WebSocket payload dissection (Wireshark handles unmasking)
local ws_binary_dissector = DissectorTable.get("ws.protocol")
if ws_binary_dissector then
    ws_binary_dissector:add("binary", ave)
end

-- Register as a heuristic on WebSocket payloads
local function ave_heuristic(tvb, pinfo, tree)
    local length = tvb:len()
    if length < 4 then return false end

    -- Check if first byte is STX
    if tvb(0, 1):uint() ~= STX then return false end

    -- Check for at least one ETX and EOT
    local has_etx = false
    local has_eot = false
    for i = 1, length - 1 do
        local b = tvb(i, 1):uint()
        if b == ETX then has_etx = true end
        if b == EOT then has_eot = true end
        if has_etx and has_eot then break end
    end
    if not (has_etx and has_eot) then return false end

    ave.dissector(tvb, pinfo, tree)
    return true
end

if pcall(function()
    ave:register_heuristic("ws", ave_heuristic)
end) then
    -- registered successfully
end

-- Register on TCP port 14001 with WebSocket-aware dissector
local tcp_port = DissectorTable.get("tcp.port")
tcp_port:add(14001, ave_tcp_dissector)
