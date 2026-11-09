import AppKit
import ApplicationServices
import Foundation
import CoreGraphics

func attr(_ element: AXUIElement, _ key: String) -> AnyObject? {
    var value: AnyObject?
    return AXUIElementCopyAttributeValue(element, key as CFString, &value) == .success ? value : nil
}
func string(_ element: AXUIElement, _ key: String) -> String {
    (attr(element, key) as? String) ?? ""
}
func children(_ element: AXUIElement) -> [AXUIElement] {
    (attr(element, kAXChildrenAttribute) as? [AXUIElement]) ?? []
}
func allElements(_ root: AXUIElement, limit: Int = 5000) -> [AXUIElement] {
    var result: [AXUIElement] = []
    var queue: [AXUIElement] = [root]
    var seen = 0
    while !queue.isEmpty && seen < limit {
        let element = queue.removeFirst()
        result.append(element)
        queue.append(contentsOf: children(element))
        seen += 1
    }
    return result
}
func text(_ element: AXUIElement) -> String {
    [kAXTitleAttribute, kAXDescriptionAttribute, kAXValueAttribute].map { string(element, $0) }.joined(separator: " ")
}
func find(_ elements: [AXUIElement], _ needle: String, role: String? = nil) -> AXUIElement? {
    elements.first { text($0).contains(needle) && (role == nil || string($0, kAXRoleAttribute) == role) }
}
func press(_ element: AXUIElement) -> Bool {
    AXUIElementPerformAction(element, kAXPressAction as CFString) == .success
}
func finish(_ status: String, _ message: String) -> Never {
    let data = try! JSONSerialization.data(withJSONObject: ["status": status, "message": message], options: [.sortedKeys])
    print(String(data: data, encoding: .utf8)!)
    exit(status == "claimed" || status == "already_claimed" ? 0 : 1)
}
guard CommandLine.arguments.count >= 2 else { finish("error", "缺少活动 ID") }
let offer = CommandLine.arguments[1]
let manual = CommandLine.arguments.contains("--manual")
guard let bundle = ["doubao-daily": "com.work.pc.doubao", "workbuddy-daily": "com.tencent.workbuddy.mac"][offer] else {
    finish("unsupported", "此活动尚无客户端代领适配器")
}
guard AXIsProcessTrusted() else { finish("needs_accessibility", "请在系统设置中为运行本助手的终端授予辅助功能权限") }
let activityTypes: [CGEventType] = [.mouseMoved, .leftMouseDown, .rightMouseDown, .keyDown, .scrollWheel]
let idleSeconds = activityTypes.map { CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: $0) }.min() ?? 0
if !manual && idleSeconds < 120 { finish("deferred", "正在使用电脑；为避免打断操作，空闲两分钟后再试") }
guard let running = NSRunningApplication.runningApplications(withBundleIdentifier: bundle).first else {
    finish("deferred", "客户端尚未运行；后台任务不会主动弹出窗口")
}
let root = AXUIElementCreateApplication(running.processIdentifier)
var elements = allElements(root)
if offer == "doubao-daily" {
    if find(elements, "签到成功") != nil || find(elements, "今日已签到") != nil { finish("already_claimed", "客户端显示今天已签到") }
    guard let button = find(elements, "立即签到", role: kAXButtonRole) else { finish("needs_review", "找不到今日签到按钮；请检查登录状态或客户端版本") }
    guard press(button) else { finish("needs_review", "签到按钮未响应") }
    for _ in 0..<10 {
        Thread.sleep(forTimeInterval: 0.7)
        elements = allElements(root)
        if let success = find(elements, "签到成功") { finish("claimed", text(success)) }
    }
    finish("needs_review", "点击后未看到签到成功反馈，未记录为已领取")
}
if find(elements, "领取成功") != nil || find(elements, "今日已领") != nil { finish("already_claimed", "客户端显示今天已领取") }
if let station = find(elements, "Buddy加油站", role: kAXButtonRole) { _ = press(station) }
elements = allElements(root)
guard find(elements, "每日可领") != nil, let button = find(elements, "立即领取", role: kAXButtonRole) else {
    finish("needs_review", "Buddy 加油站的每日领取入口不可见；请检查登录状态或客户端版本")
}
guard press(button) else { finish("needs_review", "领取按钮未响应") }
for _ in 0..<10 {
    Thread.sleep(forTimeInterval: 0.7)
    elements = allElements(root)
    if let success = find(elements, "领取成功") { finish("claimed", text(success)) }
}
finish("needs_review", "点击后未看到领取成功反馈，未记录为已领取")
