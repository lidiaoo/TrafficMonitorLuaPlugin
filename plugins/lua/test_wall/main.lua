name="test_wall"
sample="🇯🇵｜FaceBook ⚠️397ms"
interval=3

function onUpdate()
-- 使用插件提供的 tf.runCmdLine() 函数
-- 不会弹出终端窗口，同步执行，等待命令完成后返回
    local cmd = "set PYTHONIOENCODING=utf-8 && python %USERPROFILE%\\test_wall\\test_wall.py"

    local out = tf.runCmdLine(cmd)

    out = out:gsub("[\r\n]", "")

    local logFile = io.open("%USERPROFILE%\\test_wall\\traffic_monitor.log", "a")
    if logFile then
        logFile:write(os.date("%H:%M:%S") .. " - 输出: [" .. out .. "]\n")
        logFile:close()
    end

    if out == "" or out == nil then
        return "无响应"
    end

    return out
end

function onClick()
end