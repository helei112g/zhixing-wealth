import AppKit
import UserNotifications

final class NotificationAgent: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    let center = UNUserNotificationCenter.current()
    var config: [String: Any] = [:]
    var output: URL?
    var finished = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        guard let url = Bundle.main.url(forResource: "config", withExtension: "json"),
              let data = try? Data(contentsOf: url), let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { NSApp.terminate(nil); return }
        config = value; center.delegate = self
        guard CommandLine.arguments.count > 1 else {
            DispatchQueue.main.asyncAfter(deadline: .now() + 8) { if !self.finished { NSApp.terminate(nil) } }; return
        }
        let request = URL(fileURLWithPath: CommandLine.arguments[1]).standardizedFileURL
        guard request.deletingLastPathComponent().path == config["ipcDirectory"] as? String,
              request.lastPathComponent.hasSuffix(".request.json"),
              let bytes = try? Data(contentsOf: request), let command = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any] else { NSApp.terminate(nil); return }
        output = request.deletingPathExtension().appendingPathExtension("result.json")
        switch command["command"] as? String {
        case "authorize": center.requestAuthorization(options: [.alert, .sound]) { _, error in
            if let error = error { self.finish(["error": error.localizedDescription]) } else { self.status() }
        }
        case "cancel": center.removeAllPendingNotificationRequests(); center.removeAllDeliveredNotifications(); status()
        case "sync": sync(command["requests"] as? [[String: Any]] ?? [], activeSchedules: command["active_schedules"] as? [String] ?? [])
        case "test":
            let content = UNMutableNotificationContent(); content.title = "知行财富 · 通知测试"; content.body = "系统通知测试，请回到页面确认是否看到此消息。"; content.sound = .default
            center.add(UNNotificationRequest(identifier: "family-test", content: content, trigger: UNTimeIntervalNotificationTrigger(timeInterval: 3, repeats: false))) { error in
                if let error = error { self.finish(["error": error.localizedDescription]) } else { self.status() }
            }
        default: status()
        }
    }

    func sync(_ requests: [[String: Any]], activeSchedules: [String]) {
        center.getPendingNotificationRequests { pending in
            let ids = Set(requests.compactMap { $0["id"] as? String })
            self.center.removePendingNotificationRequests(withIdentifiers: pending.map { $0.identifier }.filter { !ids.contains($0) })
            self.center.getDeliveredNotifications { delivered in
                self.center.removeDeliveredNotifications(withIdentifiers: delivered.filter { notification in
                    let info = notification.request.content.userInfo
                    return !activeSchedules.contains((info["schedule_id"] as? String ?? "") + ":" + String(info["schedule_version"] as? Int ?? 0))
                }.map { $0.request.identifier })
            }
            let existing = Set(pending.map { $0.identifier }); let group = DispatchGroup()
            let lock = NSLock(); var failures: [String] = []; var requestErrors: [[String: Any]] = []
            for row in requests {
                guard let id = row["id"] as? String, !existing.contains(id), let stamp = row["at"] as? String,
                      let date = ISO8601DateFormatter().date(from: stamp) else { continue }
                let content = UNMutableNotificationContent(); content.title = row["title"] as? String ?? "知行财富"; content.body = row["body"] as? String ?? "资产提醒"; content.sound = .default
                content.userInfo = ["schedule_id": row["schedule_id"] as? String ?? "", "schedule_version": row["schedule_version"] as? Int ?? 1, "reminder_id": row["reminder_id"] as? String ?? "", "route": row["route"] as? String ?? "family", "portfolio_id": row["portfolio_id"] as? String ?? ""]
                var calendar = Calendar(identifier: .gregorian); calendar.timeZone = TimeZone(identifier: "Asia/Shanghai")!
                var components = calendar.dateComponents([.year, .month, .day, .hour, .minute, .second], from: date); components.timeZone = calendar.timeZone
                group.enter()
                self.center.add(UNNotificationRequest(identifier: id, content: content, trigger: UNCalendarNotificationTrigger(dateMatching: components, repeats: false))) { error in
                    if let error = error {
                        let detail = error as NSError
                        lock.lock(); failures.append(error.localizedDescription)
                        requestErrors.append(["id": id, "domain": detail.domain, "code": detail.code, "message": detail.localizedDescription]); lock.unlock()
                    }; group.leave()
                }
            }
            group.notify(queue: .main) { self.status(failures, requestErrors: requestErrors) }
        }
    }

    func status(_ failures: [String] = [], requestErrors: [[String: Any]] = []) {
        center.getNotificationSettings { settings in
            self.center.getPendingNotificationRequests { pending in
                self.center.getDeliveredNotifications { delivered in
                let names: [UNAuthorizationStatus: String] = [.notDetermined: "not_requested", .denied: "denied", .authorized: "authorized", .provisional: "provisional"]
                self.finish(["authorization": names[settings.authorizationStatus] ?? "unknown", "alerts": settings.alertSetting.rawValue,
                             "pending_ids": pending.map { $0.identifier }, "delivered_ids": delivered.map { $0.request.identifier }, "failures": failures, "request_errors": requestErrors])
                }
            }
        }
    }

    func finish(_ result: [String: Any]) {
        DispatchQueue.main.async {
            if let url = self.output, let data = try? JSONSerialization.data(withJSONObject: result) { try? data.write(to: url, options: .atomic) }
            self.finished = true; NSApp.terminate(nil)
        }
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification, withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) { completionHandler([.banner, .sound]) }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse, withCompletionHandler completionHandler: @escaping () -> Void) {
        guard let python = config["python"] as? String, let launcher = config["launcher"] as? String,
              let environment = config["environment"] as? String, let data = config["dataDirectory"] as? String, let port = config["port"] as? Int else { completionHandler(); return }
        let schedule = response.notification.request.content.userInfo["schedule_id"] as? String ?? ""
        let safeID = schedule.filter { $0.isHexDigit }
        let rawFocus = response.notification.request.content.userInfo["reminder_id"] as? String ?? "family:\(safeID)"
        let safeFocus = rawFocus.filter { "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:_-".contains($0) }
        let destination = "?reminders=1&focus=\(safeFocus)"
        let process = Process(); process.executableURL = URL(fileURLWithPath: python)
        process.arguments = [launcher, "--environment", environment, "--data-dir", data, "--no-open"]
        process.standardOutput = FileHandle.nullDevice; process.standardError = FileHandle.nullDevice; try? process.run()
        self.finished = true
        DispatchQueue.global().async {
            let health = URL(string: "http://127.0.0.1:\(port)/api/health")!
            for _ in 0..<40 {
                if let bytes = try? Data(contentsOf: health), let state = try? JSONSerialization.jsonObject(with: bytes) as? [String: Any], state["data_directory"] as? String == data {
                    DispatchQueue.main.async { NSWorkspace.shared.open(URL(string: "http://127.0.0.1:\(port)/\(destination)")!); NSApp.terminate(nil) }; return
                }
                Thread.sleep(forTimeInterval: 0.25)
            }
            DispatchQueue.main.async {
                let alert = NSAlert(); alert.messageText = "知行财富暂未启动"
                alert.informativeText = "请使用对应环境的启动入口打开账本。未打开其他数据目录或端口上的页面。"
                alert.runModal(); NSApp.terminate(nil)
            }
        }
        completionHandler()
    }
}

let application = NSApplication.shared
let delegate = NotificationAgent()
application.delegate = delegate
application.setActivationPolicy(.accessory)
application.run()
