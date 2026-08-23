using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Xna.Framework;
using StardewModdingAPI;
using StardewModdingAPI.Events;
using StardewValley;
using StardewValley.Buildings;
using StardewValley.Locations;
using StardewValley.TerrainFeatures;
using StardewValley.Tools;
using StardewValley.Characters;
using Microsoft.Xna.Framework.Input;
using StardewValley.Menus;
using System.Runtime.InteropServices;
using xTile.Dimensions;
using StardewValley.Network;
using StardewValley.Monsters;
using StardewValley.Minigames;
using Lidgren.Network;
using HarmonyLib;
using Chest = StardewValley.Objects.Chest;

namespace NagiBridge;

/// <summary>
/// 后台失焦冻结修复（2026-08-11）：
/// 双开实例时后台窗口被游戏判定"不激活"(Game.IsActive=false)，导致：
///  - Game1.Update 把键盘/鼠标/手柄状态清零（菜单点击/输入全失效）
///  - updateActiveMenu 只跑 update、不转发点击/按键
///  - 吃东西动画结算(doneEating) 不可靠 → farmhand 回血不生效
/// 反编译确认整条链：Game1.IsActive → InstanceGame.IsActive → GameRunner.instance.IsActive
///   → 全部汇流到 MonoGame 的 Microsoft.Xna.Framework.Game.get_IsActive 这一个 getter，
///   patch 它一处即可全覆盖。
/// 条件：脚本通过 /set_pause 关闭"失焦暂停"(pauseWhenOutOfFocus=false) 时强制返回 true，
///   脚本用 try/finally 管理 set_pause，天然不会误伤；默认值时行为完全不变。
/// 安全：MonoGame 键盘走 SDL 事件只投递给聚焦窗口，后台窗口读到的键盘永远是空的，
///   不会镜像恒(前台窗口)的按键。
/// </summary>
[HarmonyPatch(typeof(Microsoft.Xna.Framework.Game), nameof(Microsoft.Xna.Framework.Game.IsActive), MethodType.Getter)]
internal static class GameIsActivePatch
{
    internal static void Postfix(ref bool __result)
    {
        try
        {
            if (Game1.options?.pauseWhenOutOfFocus == false)
                __result = true;
        }
        catch { }
    }
}

/// <summary>
/// 检测其他玩家（恒）发表情 → 推 recent_events（MCP 端当新消息看）。
/// performPlayerEmote 在联机时对所有 farmhand 实例同步触发（恒发表情 → AI 进程里恒的 farmer 也会跑）。
/// 过滤掉 Game1.player（自己发的），其余就是别人发的。
/// </summary>
[HarmonyPatch(typeof(Farmer), nameof(Farmer.performPlayerEmote))]
internal static class OtherEmotePatch
{
    internal static void Postfix(Farmer __instance, string emote_string)
    {
        try
        {
            if (Game1.player == null || __instance == Game1.player) return; // 自己发的不管
            if (ModEntry.Instance == null) return;
            var display = ModEntry.EmoteDisplayName(emote_string);
            ModEntry.Instance.AddRecentEvent("emote", $"💬 {__instance.Name} 发了{display}", Game1.ticks);
        }
        catch { }
    }
}

/// <summary>
/// 检测其他玩家（恒）的游戏聊天 → 推 recent_events（MCP 端当新消息看，进会话缓冲）。
/// ChatBox.receiveChatMessage 在联机时对所有 farmhand 进程同步触发（恒发聊天 → AI 进程也跑）。
/// sourceFarmer 是完整 UniqueMultiplayerID（Int64，反射确认 2026-08-14）；过滤自己发的（Game1.player）和命令。
/// </summary>
[HarmonyPatch(typeof(ChatBox), nameof(ChatBox.receiveChatMessage))]
internal static class OtherChatPatch
{
    internal static void Postfix(long sourceFarmer, string message)
    {
        try
        {
            if (Game1.player == null || ModEntry.Instance == null) return;
            if (string.IsNullOrEmpty(message)) return;
            // 诊断日志：打印到达的聊天 + 过滤决策（2026-08-14 排查用）
            var selfId = Game1.player.UniqueMultiplayerID;
            if (sourceFarmer == selfId) return; // 自己发的（AI 自己/房主本机）
            if (message.StartsWith("/")) return; // 命令不记
            string sender = null;
            foreach (var f in Game1.getAllFarmers())
            {
                if (f.UniqueMultiplayerID == sourceFarmer) { sender = f.Name; break; }
            }
            if (string.IsNullOrEmpty(sender))
            {
                // src=0 = 游戏生成的系统消息（xx加入/xx躺下/xx今天早早结束了）→ 推为 📢 系统事件。
                // 过滤掉关于自己（轮回自己发起的睡/加入，AI 自己知道，别重复推）；"恒今天早早结束了"这类对 AI 有用。
                if (sourceFarmer == 0)
                {
                    var localName = Game1.player?.Name;
                    if (!string.IsNullOrEmpty(localName) && message.Contains(localName))
                        return;
                    ModEntry.Instance.AddRecentEvent("chat", $"📢 {message}", Game1.ticks);
                    return;
                }
                ModEntry.Instance.Monitor.Log($"[chat] 收到聊天但找不到发送者: src={sourceFarmer} self={selfId} msg={message}", LogLevel.Debug);
                return;
            }
            ModEntry.Instance.AddRecentEvent("chat", $"💬 {sender}: {message}", Game1.ticks);
            ModEntry.Instance.Monitor.Log($"[chat] 记录: {sender}: {message}", LogLevel.Info);
        }
        catch (Exception ex)
        {
            ModEntry.Instance?.Monitor.Log($"[chat] 异常: {ex.Message}", LogLevel.Warn);
        }
    }
}

/// <summary>
/// 抓取游戏原生节日提示（"花舞节已经在森林里开始举办了。"）→ 推 recent_events 小新闻。
/// AI 看到 📢 就知道节日开始了、去参加；书摊老板等零碎节日事件也一并推送（用户 2026-08-14）。
/// 兜底：/festival 的 isFestival 也能查实况。
/// </summary>
[HarmonyPatch(typeof(Game1), nameof(Game1.showGlobalMessage))]
internal static class FestivalMsgPatch
{
    internal static void Postfix(string message)
    {
        try
        {
            if (ModEntry.Instance == null || string.IsNullOrEmpty(message)) return;
            if (!IsFestivalMessage(message)) return;
            ModEntry.Instance.AddRecentEvent("news", $"📢 {message}", Game1.ticks);
            ModEntry.Instance.Monitor.Log($"[festival] 捕获原生提示: {message}", LogLevel.Debug);
        }
        catch (Exception ex)
        {
            ModEntry.Instance?.Monitor.Log($"[festival] 捕获异常: {ex.Message}", LogLevel.Warn);
        }
    }

    private static bool IsFestivalMessage(string msg)
    {
        // 节日开始/结束提示含"举办/开始了/结束"；或点名节日/书摊
        if (msg.Contains("举办") || msg.Contains("开始了") || msg.Contains("结束了")) return true;
        // 🆕 2026-08-18 升级工具完成（"你的XXX升级好了"）——只在完成的那个早晨播一次；
        //     错过时机靠 /state.player.toolUpgrading（toolBeingUpgraded 非空）随时可查
        if (msg.Contains("升级")) return true;
        foreach (var name in new[] { "蛋蛋节", "花舞节", "沙漠节", "夏威夷宴会", "鳟鱼大赛", "月光水母",
                                     "秋收节", "万灵节", "冬钓节", "鱿鱼节", "夜市", "冬星节", "书摊" })
        {
            if (msg.Contains(name)) return true;
        }
        return false;
    }
}

/// <summary>🏆 成就弹窗即时播报（2026-08-23 恒）：patch Game1.addHUDMessage，抓 message.achievement==true 的
/// 真实 toast（HUDMessage.ForAchievement：achievement=true, whatType=1，反编译 HUDMessage.cs:115）。
/// 反编译确认 getAchievement 内部无条件调 addHUDMessage(ForAchievement(name))（Game1:10638）——所以
/// patch **addHUDMessage** 才是抓"游戏真正渲染给玩家"的入口，比 patch getAchievement（含读档/补触发逻辑）干净，
/// 也不需要自己造 gameMode/静默窗口那类旁路条件。
/// ⚠️ 联机广播（globalChatInfoMessage/chatsync）不走 addHUDMessage 成就 toast → 天然只抓本进程真弹的。
/// ⚠️ 双开各进程 Game1.player=各自角色，各弹各的。</summary>
[HarmonyPatch(typeof(Game1), nameof(Game1.addHUDMessage))]
internal static class AchievementToastPatch
{
    internal static void Postfix(HUDMessage message)
    {
        try
        {
            if (ModEntry.Instance == null || message == null) return;
            if (!message.achievement) return;   // 只抓成就 toast（非新手/角色/提示等）
            // message.message = LoadString("Strings\StringsFromCSFiles:HUDMessage.cs.3824") + 成就名。
            // 中文版前缀="新成就："（全角冒号），英文="Achievement!"/"New achievement!"。剥前缀只留成就名。
            string name = message.message ?? "";
            // 完整前缀优先（中文全角冒号 / 英文），再逐字符兜底剥到分隔符
            foreach (var prefix in new[] { "新成就：", "新成就:", "新成就！", "新成就!", "Achievement!", "New Achievement!", "New achievement!" })
            {
                if (name.StartsWith(prefix)) { name = name.Substring(prefix.Length).Trim(); break; }
            }
            foreach (var sep in new[] { "：", "!", "！", "。", ":" })
            {
                int i = name.IndexOf(sep);
                if (i >= 0 && i < name.Length - 1) { name = name.Substring(i + sep.Length).Trim(); break; }
            }
            if (string.IsNullOrWhiteSpace(name)) name = message.message ?? "成就";
            // 玩家名：Game1.player 可能为 null/空（非玩家成就 toast）→ 兜底谁都不标明
            string who = Game1.player?.Name ?? "";
            if (string.IsNullOrEmpty(who)) who = "";
            ModEntry.Instance.AddRecentEvent("achievement", $"🏆 {(who.Length > 0 ? who + " " : "")}达成成就: {name}", Game1.ticks);
            ModEntry.Instance.Monitor.Log($"[achievement] toast: {who} 达成 {name}", LogLevel.Debug);
        }
        catch (Exception ex)
        {
            ModEntry.Instance?.Monitor.Log($"[achievement] 捕获异常: {ex.Message}", LogLevel.Warn);
        }
    }
}

/// <summary>⚠️ 10048 端口冲突修复（2026-08-19）：双开（host 7842 + farmhand 7843）同机时，
/// farmhand 的 LidgrenServer.initialize() 硬绑端口 24642（被 host 占用）→ SocketException 10048
/// → farmhand 的 GameServer 起不来（网络层不完整，Proposal 应答传不回 host）。
/// prefix 重实现：24642 被占就换第一个空闲端口（host 单开仍用 24642 不受影响）。
/// 失败时退回原方法（原样报错，不更糟）。</summary>
internal static class LidgrenServerPortPatch
{
    internal static bool Prefix(LidgrenServer __instance)
    {
        try
        {
            int port = FindFreeUdpPort(LidgrenServer.defaultPort);
            var cfg = new NetPeerConfiguration("StardewValley");
            cfg.EnableMessageType(NetIncomingMessageType.DiscoveryRequest);
            cfg.EnableMessageType(NetIncomingMessageType.ConnectionApproval);
            cfg.Port = port;
            cfg.ConnectionTimeout = 30f;
            cfg.PingInterval = 5f;
            cfg.MaximumConnections = GetPlayerLimit() * 2;
            cfg.MaximumTransmissionUnit = 1200;
            __instance.server = new NetServer(cfg);
            __instance.server.Start();
            if (port != LidgrenServer.defaultPort)
                ModEntry.Instance?.Monitor.Log($"[portfix] 端口 {LidgrenServer.defaultPort} 被占，farmhand 服务器改用 {port}", LogLevel.Info);
            return false; // 跳过原方法（已用动态端口完成初始化）
        }
        catch (Exception ex)
        {
            ModEntry.Instance?.Monitor.Log($"[portfix] 端口补丁异常，退回原逻辑: {ex.Message}", LogLevel.Warn);
            return true; // 让原方法跑
        }
    }

    /// <summary>取玩家上限（Game1.multiplayer 是 protected，反射读；失败兜底 4→8 连接）。</summary>
    private static int GetPlayerLimit()
    {
        try
        {
            var mpField = typeof(Game1).GetField("multiplayer",
                BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic);
            var mp = mpField?.GetValue(null);
            var limitProp = mp?.GetType().GetProperty("playerLimit",
                BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            var limit = limitProp?.GetValue(mp);
            if (limit is int li && li > 0) return li;
        }
        catch { }
        return 4;
    }

    /// <summary>从 start 起找第一个能试绑成功的 UDP 端口（24642 被 host 占 → 24643+）。</summary>
    private static int FindFreeUdpPort(int start)
    {
        for (int p = start; p < start + 100; p++)
        {
            try
            {
                using var udp = new System.Net.Sockets.UdpClient(new IPEndPoint(IPAddress.Any, p));
                return p; // 试绑成功 = 空闲
            }
            catch
            {
                // 被占，试下一个
            }
        }
        return start;
    }
}


public class ModEntry : Mod
{
    /// <summary>构建标记（防倒退：/status 报这个，部署/重启后核对，旧 DLL/原作者版会不同）。</summary>
    public const string BuildStamp = "2026-08-18-dialog-advance";

    /// <summary>当前 ModEntry 实例（Harmony 补丁等静态代码需要调实例方法时用）。</summary>
    internal static ModEntry? Instance;

    [DllImport("user32.dll")] private static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);
    private const uint KEYEVENTF_KEYUP = 0x0002;
    [DllImport("user32.dll")] private static extern bool SetCursorPos(int X, int Y);
    [DllImport("user32.dll")] private static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, UIntPtr dwExtraInfo);
    [DllImport("user32.dll")] private static extern bool SetForegroundWindow(IntPtr hWnd);
    private const uint MOUSEEVENTF_LEFTDOWN = 0x0002;
    private const uint MOUSEEVENTF_LEFTUP = 0x0004;

    private HttpListener? _listener;
    private CancellationTokenSource? _cts;
    private readonly Queue<Action> _mainThreadQueue = new();
    private readonly object _queueLock = new();
    private int _port;

    // Pathfinding state
    private Queue<Point>? _pathQueue;
    private int _pathTickCooldown;

    // Command queue state
    private Queue<Dictionary<string, object?>>? _commandQueue;
    private readonly List<object> _commandResults = new();
    private TaskCompletionSource<object>? _commandQueueTcs;
    private int _commandDelay;
    private bool _waitingForMove;
    private bool _waitingForBite;
    private int _biteTimeout;

    // Tool charge state (visual delay before direct tile modification)
    private int _toolChargeTicks;
    private bool _isChargingTool;
    private int _chargePower;   // power level to use when charge completes
    private string? _chargeOp;  // "till" or "water"

    // Tool area 蓄力补漏（取余补站位，2026-08-15）：主流程后自检漏格 → 聚矩形再蓄力补。
    private List<(int tx, int ty)> _toolAreaTargets = new();
    private string _toolAreaOperation = "till";   // 逐锚点验证用（till/water）
    private int _toolAreaToolW = 1, _toolAreaToolH = 1;
    private int _toolAreaChargeFrames = 0, _toolAreaUpgradeLevel = 0;
    private int _toolAreaTotalSwings = 0;

    // Time freeze state
    private bool _timeFrozen;
    private int _frozenTime;

    // Walk-to multi-map routing state
    private List<WalkSegment>? _walkRoute;
    private int _walkSegIdx;
    private bool _walkSegmentStarted;
    private bool _warpPending; // prevents duplicate warp queuing

    // Warp graph for cross-map pathfinding (lazy-built)
    private Dictionary<string, List<WarpLink>>? _warpGraph;

    // Alert queue for game/system feedback consumed by external agents.
    private readonly Queue<Dictionary<string, object?>> _alertQueue = new();
    private readonly Dictionary<string, DateTime> _lastAlertTimes = new();
    private readonly object _alertLock = new();
    private string? _lastMenuType;
    private string? _lastMenuText;
    private string? _lastEventId;
    private string? _lastEventText;
    private bool _lastStaminaLow;
    private bool _lastWaterEmpty;
    private bool _lastInventoryFull;

    // Recent events log (included in state response)
    private readonly List<Dictionary<string, object?>> _recentEvents = new();
    private const int MAX_RECENT_EVENTS = 20;
    private HashSet<string> _knownMail = new();
    private bool _mailInited;                       // 首次加载静默同步已读邮件，防 flood
    private bool _mailDataTried;                    // Data/Mail 只尝试加载一次
    private Dictionary<string, string>? _mailData;
    private HashSet<string> _knownMailbox = new();  // 未读邮箱队列（mailbox）快照——新未读邮件也推 📬（2026-08-15）
    private bool _mailboxInited;

    // 任务变化检测（2026-08-15 恒：任务日志=自带新手引导，新任务/完成要提醒 AI）
    private HashSet<string> _knownQuests = new();       // 当前日志里见过的任务 id
    private HashSet<string> _knownQuestDone = new();    // 已报过"完成"的任务 id（防重复报）
    private bool _questInited;                          // 首次加载静默同步，防 flood

    // 静止计时：玩家 TilePoint 未变的真实秒数（发呆检测用）
    private Point? _lastTilePoint;
    private double _lastTileChangeSeconds;
    private double _stationarySeconds;

    // ── 拾取小新闻：同批按 (itemId|quality) 归组，停顿后落一条 recent_events ──
    private readonly Dictionary<string, PickupInfo> _pendingPickups = new();
    private readonly object _pickupLock = new();
    private int _lastPickupTick = -1;
    private const int PICKUP_FLUSH_TICKS = 45;      // ~0.75s 无新拾取则落新闻
    // 物品数量快照（区分"新获得"与"挪位/换位"），初始化于世界就绪时
    private readonly Dictionary<string, int> _invCounts = new();
    private bool _invCountsInited;
    // 穿戴签名（tick 轮询，只报变化的单件；与 /state 整套穿搭摘要区分开）
    private Dictionary<string, string> _lastWorn = new();
    private int _lastWornCheckTick;

    // 描述按天去重：当天内同 (物品ID|星级) 只显示第一次（DayStarted 清空）
    private readonly HashSet<string> _descShownToday = new();
    private string? _lastDayKey;

    private class PickupInfo
    {
        public string Key = "";
        public string DisplayName = "";
        public int Quality;
        public int Value;
        public string Stats = "";
        public string Description = "";
        public int Count;
    }

    // 过夜结算（ShippingMenu）：AI 和恒的聊天窗口（游戏时间暂停）——复盘今天、商量明天，
    // 都聊完了才由 AI 调 /settlement_confirm 确认进下一天。
    // 兜底（SettlementAutoDismissMs=10分钟）：AI 挂了/MCP 断了才自动关，防结算界面卡死冻结。
    private double _settlementShownMs;
    private const double SettlementAutoDismissMs = 600000;


    // ⚠️ 2026-08-14 撤除：host 自动触发 Sleep_Yes 方案已被证伪——
    //    会抢在 farmhand(7843) 同步前触发假过夜（秋17误报），制造假象，必须不用。
    //    反向一起睡（恒爬 farmhand 床）= SDV 原生限制（问题对话框不弹→Sleep_Yes 到不了）。

    private record WalkSegment(string Location, int TargetX, int TargetY);
    private record WarpLink(string TargetLocation, int SourceX, int SourceY, int DestX, int DestY);

    private ChatHud? _chatHud;
    private ModConfig? _modConfig;
    private LlmClient? _llmClient;

    public override void Entry(IModHelper helper)
    {
        Instance = this;
        _modConfig = helper.ReadConfig<ModConfig>();
        _llmClient = new LlmClient(_modConfig, helper.DirectoryPath);

        helper.Events.GameLoop.GameLaunched += OnGameLaunched;
        helper.Events.GameLoop.SaveLoaded += OnSaveLoaded;
        helper.Events.GameLoop.UpdateTicked += OnUpdateTicked;
        helper.Events.GameLoop.ReturnedToTitle += OnReturnedToTitle;
        helper.Events.Display.RenderedHud += OnRenderedHud;
        helper.Events.Display.Rendered += OnRendered;
        helper.Events.Input.ButtonPressed += OnButtonPressed;
        helper.Events.Player.InventoryChanged += OnInventoryChanged;

        _chatHud = new ChatHud(Monitor, OnChatSend, OnApiConfigured, OnChannelSelected);
        _chatHud.SetInitialState(_modConfig.Mode, _modConfig.ApiKey, _modConfig.ApiUrl);
    }

    private void OnApiConfigured(string apiKey, string apiUrl)
    {
        _modConfig!.ApiKey = apiKey;
        _modConfig.ApiUrl = apiUrl;
        _modConfig.Mode = "api";
        if (apiUrl.Contains("deepseek")) _modConfig.ApiProvider = "deepseek";
        else if (apiUrl.Contains("anthropic")) _modConfig.ApiProvider = "claude";
        else if (apiUrl.Contains("openai.com")) _modConfig.ApiProvider = "openai";
        else _modConfig.ApiProvider = "custom";
        _llmClient = new LlmClient(_modConfig, Helper.DirectoryPath);
        Helper.WriteConfig(_modConfig);
        Monitor.Log($"API configured, provider={_modConfig.ApiProvider}, url={apiUrl}", LogLevel.Info);
    }

    private void OnChannelSelected()
    {
        _modConfig!.Mode = "cc";
        Helper.WriteConfig(_modConfig);
        Monitor.Log($"Channel mode selected", LogLevel.Info);
    }

    private void OnChatSend(string text)
    {
        Task.Run(async () =>
        {
            try
            {
                if (_modConfig!.Mode.Equals("cc", StringComparison.OrdinalIgnoreCase))
                {
                    using var client = new HttpClient();
                    var json = JsonSerializer.Serialize(new { message = text });
                    var content = new StringContent(json, Encoding.UTF8, "application/json");
                    await client.PostAsync(_modConfig.ChannelServerUrl, content);
                }
                else
                {
                    var reply = await _llmClient!.SendAsync(text);
                    _chatHud?.AddMessage(_chatHud.AiDisplayName, reply);
                }
            }
            catch (Exception ex)
            {
                Monitor.Log($"Chat send error: {ex.Message}", LogLevel.Debug);
            }
        });
    }

    private void OnRenderedHud(object? sender, RenderedHudEventArgs e)
    {
        _chatHud?.DrawHud(e.SpriteBatch);
    }

    private void OnRendered(object? sender, RenderedEventArgs e)
    {
        _chatHud?.DrawPanel(e.SpriteBatch);
    }

    private void OnButtonPressed(object? sender, ButtonPressedEventArgs e)
    {
        if (e.Button == StardewModdingAPI.SButton.OemTilde)
            Helper.Input.Suppress(e.Button);
        if (_chatHud?.IsOpen == true)
            Helper.Input.Suppress(e.Button);
    }

    private void OnGameLaunched(object? sender, GameLaunchedEventArgs e)
    {
        // ⚠️ 后台失焦冻结修复（2026-08-11）：patch Game.IsActive getter。
        // 双开实例时后台窗口被判定不激活 → 输入清零/菜单点击失效/吃动画结算不可靠。
        // 反编译确认 Game1.IsActive 全链路汇流到 MonoGame 的 Game.get_IsActive，patch 它一处全覆盖。
        try
        {
            var harmony = new Harmony("wingheng.NagiBridge");
            var isActiveGetter = AccessTools.PropertyGetter(typeof(Microsoft.Xna.Framework.Game), "IsActive");
            if (isActiveGetter == null)
            {
                Monitor.Log("Harmony: 找不到 Game.IsActive getter，后台失焦修复未生效！", LogLevel.Error);
            }
            else
            {
                var postfix = AccessTools.Method(typeof(GameIsActivePatch), nameof(GameIsActivePatch.Postfix));
                harmony.Patch(isActiveGetter, postfix: new HarmonyMethod(postfix));
                Monitor.Log($"Harmony: Game.IsActive 补丁已应用 ({isActiveGetter.FullDescription()})", LogLevel.Info);

                // 恒的表情检测：postfix performPlayerEmote（其他玩家发表情时同步触发）
                var emoteMethod = AccessTools.Method(typeof(Farmer), nameof(Farmer.performPlayerEmote));
                if (emoteMethod != null)
                {
                    var emotePostfix = AccessTools.Method(typeof(OtherEmotePatch), nameof(OtherEmotePatch.Postfix));
                    harmony.Patch(emoteMethod, postfix: new HarmonyMethod(emotePostfix));
                    Monitor.Log("Harmony: performPlayerEmote 补丁已应用（恒的表情检测）", LogLevel.Info);
                }
                else
                {
                    Monitor.Log("Harmony: 找不到 performPlayerEmote，恒的表情检测未生效", LogLevel.Error);
                }

                // 恒的聊天检测：postfix receiveChatMessage（联机时所有 farmhand 进程同步触发）
                var chatMethod = AccessTools.Method(typeof(ChatBox), nameof(ChatBox.receiveChatMessage));
                if (chatMethod != null)
                {
                    var chatPostfix = AccessTools.Method(typeof(OtherChatPatch), nameof(OtherChatPatch.Postfix));
                    harmony.Patch(chatMethod, postfix: new HarmonyMethod(chatPostfix));
                    Monitor.Log("Harmony: receiveChatMessage 补丁已应用（恒的聊天检测）", LogLevel.Info);
                }
                else
                {
                    Monitor.Log("Harmony: 找不到 receiveChatMessage，恒的聊天检测未生效", LogLevel.Error);
                }

                // 节日原生提示抓取：postfix showGlobalMessage（"花舞节已经在森林里开始举办了。"）
                var festMethod = AccessTools.Method(typeof(Game1), nameof(Game1.showGlobalMessage));
                if (festMethod != null)
                {
                    var festPostfix = AccessTools.Method(typeof(FestivalMsgPatch), nameof(FestivalMsgPatch.Postfix));
                    harmony.Patch(festMethod, postfix: new HarmonyMethod(festPostfix));
                    Monitor.Log("Harmony: showGlobalMessage 补丁已应用（节日原生提示抓取）", LogLevel.Info);
                }
                else
                {
                    Monitor.Log("Harmony: 找不到 showGlobalMessage，节日提示抓取未生效", LogLevel.Error);
                }

                // ⚠️ 10048 端口冲突修复（2026-08-19）：farmhand 的 LidgrenServer 硬绑 24642 被 host 占 → 10048。
                //    prefix 换成空闲端口（host 用 24642 不变；见 LidgrenServerPortPatch）。
                var lidgrenMethod = AccessTools.Method(typeof(LidgrenServer), "initialize");
                if (lidgrenMethod != null)
                {
                    var lidgrenPrefix = AccessTools.Method(typeof(LidgrenServerPortPatch), nameof(LidgrenServerPortPatch.Prefix));
                    harmony.Patch(lidgrenMethod, prefix: new HarmonyMethod(lidgrenPrefix));
                    Monitor.Log("Harmony: LidgrenServer.initialize 补丁已应用（10048 端口冲突修复）", LogLevel.Info);
                }
                else
                {
                    Monitor.Log("Harmony: 找不到 LidgrenServer.initialize，端口修复未生效", LogLevel.Error);
                }

                // 🏆 成就弹窗播报（2026-08-23 恒）：patch Game1.addHUDMessage，抓 message.achievement=true 的真实 toast
                var toastMethod = AccessTools.Method(typeof(Game1), "addHUDMessage");
                if (toastMethod != null)
                {
                    var toastPostfix = AccessTools.Method(typeof(AchievementToastPatch), nameof(AchievementToastPatch.Postfix));
                    harmony.Patch(toastMethod, postfix: new HarmonyMethod(toastPostfix));
                    Monitor.Log("Harmony: Game1.addHUDMessage 补丁已应用（成就 toast 播报）", LogLevel.Info);
                }
                else
                {
                    Monitor.Log("Harmony: 找不到 Game1.addHUDMessage，成就 toast 播报未生效", LogLevel.Error);
                }
            }
        }
        catch (Exception ex)
        {
            Monitor.Log($"Harmony 补丁注册失败: {ex.Message}", LogLevel.Error);
        }

        // ⚠️ 失焦修复增强（2026-08-13 恒：AI 自动化后台跑，游戏重启后 pauseWhenOutOfFocus 还原 true
        //    → 后台暂停，warp/开门全卡。启动+读档自动设 false，后台不暂停。）
        try { Game1.options.pauseWhenOutOfFocus = false; }
        catch (Exception ex) { Monitor.Log($"设 pauseWhenOutOfFocus 失败: {ex.Message}", LogLevel.Error); }

        // 应用持久分辨率（WindowWidth/WindowHeight，启动时设置——比中途 ApplyChanges 稳定）
        try
        {
            if (_modConfig != null && _modConfig.WindowWidth > 0 && _modConfig.WindowHeight > 0)
            {
                Game1.graphics.PreferredBackBufferWidth = _modConfig.WindowWidth;
                Game1.graphics.PreferredBackBufferHeight = _modConfig.WindowHeight;
                Game1.graphics.ApplyChanges();
            }
        }
        catch (Exception ex)
        {
            Monitor.Log($"设置分辨率失败: {ex.Message}", LogLevel.Warn);
        }

        StartServer();
    }

    private void OnSaveLoaded(object? sender, SaveLoadedEventArgs e)
    {
        // ⚠️ 失焦修复（2026-08-13）：读档后 pauseWhenOutOfFocus 可能还原 true → 后台暂停卡 AI。
        //    强制 false，让 AI 自动化后台正常运行（warp/开门不卡）。
        try { Game1.options.pauseWhenOutOfFocus = false; }
        catch (Exception ex) { Monitor.Log($"SaveLoaded 设 pauseWhenOutOfFocus 失败: {ex.Message}", LogLevel.Error); }
    }

    private void OnReturnedToTitle(object? sender, ReturnedToTitleEventArgs e)
    {
        ClearMovementState();
        _lastTilePoint = null;
        _stationarySeconds = 0;
        _invCounts.Clear();
        _invCountsInited = false;
        _lastWorn = new Dictionary<string, string>();
        _descShownToday.Clear();
        _lastDayKey = null;
        _knownMail = new HashSet<string>();
        _mailInited = false;
        _mailDataTried = false;
        _mailData = null;
        _knownMailbox = new HashSet<string>();
        _mailboxInited = false;
        _knownQuests = new HashSet<string>();
        _knownQuestDone = new HashSet<string>();
        _questInited = false;
    }

    private void ClearMovementState()
    {
        _pathQueue = null;
        _pathTickCooldown = 0;
        _waitingForMove = false;
    }

    private void CenterViewportOnFarmer(Farmer farmer)
    {
        var loc = farmer.currentLocation;
        int viewW = Game1.viewport.Width;
        int viewH = Game1.viewport.Height;
        int maxX = Math.Max(0, loc.Map.DisplayWidth - viewW);
        int maxY = Math.Max(0, loc.Map.DisplayHeight - viewH);
        int vx = (int)farmer.Position.X - viewW / 2;
        int vy = (int)farmer.Position.Y - viewH / 2;

        Game1.viewport.X = Math.Max(0, Math.Min(maxX, vx));
        Game1.viewport.Y = Math.Max(0, Math.Min(maxY, vy));
    }

    private void EnqueueAlert(string type, string message, string severity = "info", string source = "bridge")
    {
        if (string.IsNullOrWhiteSpace(message))
            return;

        var now = DateTime.UtcNow;
        var key = $"{type}:{message}";

        lock (_alertLock)
        {
            if (_lastAlertTimes.TryGetValue(key, out var last) && (now - last).TotalSeconds < 4)
                return;
            _lastAlertTimes[key] = now;

            _alertQueue.Enqueue(new Dictionary<string, object?>
            {
                ["timeUtc"] = now.ToString("O"),
                ["type"] = type,
                ["severity"] = severity,
                ["source"] = source,
                ["message"] = message
            });

            while (_alertQueue.Count > 100)
                _alertQueue.Dequeue();

            foreach (var stale in _lastAlertTimes.Where(p => (now - p.Value).TotalMinutes > 5).Select(p => p.Key).ToList())
                _lastAlertTimes.Remove(stale);
        }
    }

    private void CaptureAlerts()
    {
        var farmer = Game1.player;
        if (farmer == null)
            return;

        if (Game1.hudMessages != null)
        {
            foreach (var hud in Game1.hudMessages)
            {
                var text = hud.message;
                if (!string.IsNullOrWhiteSpace(text))
                    EnqueueAlert("hud", text, "info", "hud");
            }
        }

        bool staminaLow = farmer.MaxStamina > 0 && farmer.Stamina / farmer.MaxStamina < 0.15f;
        if (staminaLow && !_lastStaminaLow)
            EnqueueAlert("stamina_low", $"Stamina low: {farmer.Stamina:0}/{farmer.MaxStamina:0}", "warning", "state");
        else if (!staminaLow && _lastStaminaLow)
            EnqueueAlert("stamina_ok", $"Stamina recovered: {farmer.Stamina:0}/{farmer.MaxStamina:0}", "info", "state");
        _lastStaminaLow = staminaLow;

        var wateringCan = farmer.Items.OfType<WateringCan>().FirstOrDefault();
        bool waterEmpty = wateringCan != null && wateringCan.WaterLeft <= 0;
        if (waterEmpty && !_lastWaterEmpty)
            EnqueueAlert("water_empty", "Watering can is empty", "warning", "state");
        else if (!waterEmpty && _lastWaterEmpty)
            EnqueueAlert("water_refilled", "Watering can has water", "info", "state");
        _lastWaterEmpty = waterEmpty;

        int usedSlots = farmer.Items.Count(item => item != null);
        bool inventoryFull = usedSlots >= farmer.MaxItems;
        if (inventoryFull && !_lastInventoryFull)
            EnqueueAlert("inventory_full", $"Inventory full: {usedSlots}/{farmer.MaxItems}", "warning", "state");
        else if (!inventoryFull && _lastInventoryFull)
            EnqueueAlert("inventory_space", $"Inventory has space: {usedSlots}/{farmer.MaxItems}", "info", "state");
        _lastInventoryFull = inventoryFull;

        CaptureMenuAlerts();
        CaptureEventAlerts();
        CaptureRecentEvents();
    }

    private void CaptureMenuAlerts()
    {
        var menu = Game1.activeClickableMenu;
        string? menuType = menu?.GetType().Name;
        string? menuText = null;

        if (menu is DialogueBox dialogue)
        {
            try { menuText = dialogue.getCurrentString(); } catch { }
        }
        else if (menu != null)
        {
            menuText = menuType;
        }

        if (menuType != _lastMenuType)
        {
            if (menuType == null)
                EnqueueAlert("menu_closed", "Menu closed", "info", "menu");
            else
                EnqueueAlert("menu_opened", $"Menu opened: {menuType}", "info", "menu");
            _lastMenuType = menuType;
            _lastMenuText = null;
        }

        if (!string.IsNullOrWhiteSpace(menuText) && menuText != _lastMenuText)
        {
            EnqueueAlert("menu_text", menuText, "info", "menu");
            _lastMenuText = menuText;
        }

        // 🎁 礼物接受框检测："<玩家>向你提供<物品>。你会接受吗？"
        // 用游戏原版文字 + warning 级标记，让 AI 一眼看出是礼物请求（可操作）。
        if (menu is DialogueBox && menuText != null
            && menuText.Contains("向你提供") && menuText.Contains("接受"))
        {
            EnqueueAlert("gift_prompt", $"🎁 {menuText}", "warning", "gift");
        }

        // 💤 送礼等待中：DS 发了礼物提议，等对方接受（PendingProposalDialog）
        if (menu is StardewValley.Menus.PendingProposalDialog)
        {
            EnqueueAlert("gift_pending",
                "💤 礼物提议已发送，等待对方接受（对方接受后自动完成）",
                "info", "gift");
        }
    }

    private void CaptureEventAlerts()
    {
        var ev = Game1.currentLocation?.currentEvent;
        string? eventId = ev?.id;
        string? eventText = null;

        if (ev != null && Game1.activeClickableMenu is DialogueBox dialogue)
        {
            try { eventText = dialogue.getCurrentString(); } catch { }
        }

        if (eventId != _lastEventId)
        {
            if (eventId == null)
                EnqueueAlert("event_ended", "Event ended", "info", "event");
            else
                EnqueueAlert("event_started", $"Event started: {eventId}", "info", "event");
            _lastEventId = eventId;
            _lastEventText = null;
        }

        if (!string.IsNullOrWhiteSpace(eventText) && eventText != _lastEventText)
        {
            EnqueueAlert("event_text", eventText, "info", "event");
            _lastEventText = eventText;
        }
    }

    private void CompleteCommandQueue()
    {
        _commandQueueTcs?.TrySetResult(new
        {
            ok = true,
            executed = _commandResults.Count,
            results = _commandResults.ToArray()
        });
        _commandQueue = null;
    }

    private void CaptureRecentEvents()
    {
        if (!Context.IsWorldReady || Game1.player == null) return;
        try
        {
            var farmer = Game1.player;

            // 1. 检测新邮件（首次加载静默同步，只报之后新增的；过滤事件旗标）
            if (farmer.mailReceived != null)
            {
                if (!_mailInited)
                {
                    _mailInited = true;
                    _knownMail.UnionWith(farmer.mailReceived.Where(m => !string.IsNullOrEmpty(m)));
                }
                if (!_mailDataTried)
                {
                    _mailDataTried = true;
                    try { _mailData = Game1.content.Load<Dictionary<string, string>>("Data/Mail"); }
                    catch (Exception ex)
                    {
                        Monitor.Log($"Data/Mail load fail (Game1.content): {ex.Message}", LogLevel.Warn);
                        try { _mailData = Helper.GameContent.Load<Dictionary<string, string>>("Data/Mail"); }
                        catch (Exception ex2) { Monitor.Log($"Data/Mail load fail (Helper.GameContent): {ex2.Message}", LogLevel.Warn); }
                    }
                }
                foreach (var mailId in farmer.mailReceived)
                {
                    if (string.IsNullOrEmpty(mailId) || _knownMail.Contains(mailId)) continue;
                    _knownMail.Add(mailId);
                    // 只报真信件：Data/Mail 有条目的才算（过滤 _eventSeen_/doorUnlock 等旗标）；
                    // load 失败时不拦（保持原行为，标题退回 mailId）
                    if (_mailData != null && !_mailData.ContainsKey(mailId)) continue;
                    string title = mailId;
                    if (_mailData != null && _mailData.TryGetValue(mailId, out var raw))
                    {
                        try
                        {
                            var parts = raw.Split('/');
                            // SDV Data/Mail: 0=text 1=sender 2=date 3=time 4=repeatDay 5=title
                            foreach (int idx in new[] { 5, 1 })
                            {
                                if (parts.Length > idx)
                                {
                                    var cand = parts[idx].Trim();
                                    if (cand.Length > 0 && !cand.All(char.IsDigit))
                                    {
                                        title = cand;
                                        break;
                                    }
                                }
                            }
                        }
                        catch { }
                    }
                    AddRecentEvent("mail", $"📬 收到邮件: {title}", Game1.ticks);
                }
            }

            // 1b. 未读邮箱队列（mailbox）也盯（2026-08-15 恒：day-start 来信在未读队列，recent_events 只跟踪已读
            //     mailReceived 推不到 → AI 进游戏没"新邮件"提醒。加未读队列快照：
            //     首次快照发现已有未读信 → 推一条汇总"进档提醒"（每会话一次，不逐个弹）；之后新到的信逐个推 📬(未读)。
            //     读信后移进 mailReceived 再触发时因内容相同会被 AddRecentEvent 去重。）
            try
            {
                if (farmer.mailbox != null)
                {
                    string MailTitle(string mid)
                    {
                        string t = mid;
                        if (_mailData != null && _mailData.TryGetValue(mid, out var mraw))
                        {
                            try
                            {
                                var mparts = mraw.Split('/');
                                foreach (int idx in new[] { 5, 1 })
                                {
                                    if (mparts.Length > idx)
                                    {
                                        var cand = mparts[idx].Trim();
                                        if (cand.Length > 0 && !cand.All(char.IsDigit)) { t = cand; break; }
                                    }
                                }
                            }
                            catch { }
                        }
                        return t;
                    }
                    if (!_mailboxInited)
                    {
                        _mailboxInited = true;
                        var unread = new List<string>();
                        foreach (var m in farmer.mailbox)
                        {
                            if (string.IsNullOrEmpty(m) || _knownMailbox.Contains(m)) continue;
                            _knownMailbox.Add(m);
                            unread.Add(MailTitle(m));
                        }
                        if (unread.Count > 0)
                            AddRecentEvent("mail", $"📬 邮箱有未读信: {string.Join("、", unread)}（去邮箱交互读信领附件）", Game1.ticks);
                    }
                    else
                    {
                        foreach (var m in farmer.mailbox)
                        {
                            if (string.IsNullOrEmpty(m) || _knownMailbox.Contains(m)) continue;
                            _knownMailbox.Add(m);
                            AddRecentEvent("mail", $"📬 收到邮件(未读): {MailTitle(m)}", Game1.ticks);
                        }
                    }
                }
            }
            catch { }

            // 2. 任务变化检测：新任务 / 任务完成可领奖励（2026-08-15 恒：任务日志=自带新手引导）
            try
            {
                var questNow = new Dictionary<string, (string title, bool completed)>();
                var dailyQuest = Game1.questOfTheDay;
                if (dailyQuest != null && dailyQuest.id.Value != null)
                    questNow[dailyQuest.id.Value] = (dailyQuest.questTitle, dailyQuest.completed.Value);
                if (farmer.questLog != null)
                {
                    foreach (var q in farmer.questLog)
                    {
                        if (q == null || q.id.Value == null) continue;
                        questNow[q.id.Value] = (q.questTitle, q.completed.Value);
                    }
                }
                if (!_questInited)
                {
                    _questInited = true;
                    foreach (var k in questNow.Keys) _knownQuests.Add(k);
                }
                else
                {
                    foreach (var kv in questNow)
                    {
                        if (!_knownQuests.Contains(kv.Key))
                        {
                            _knownQuests.Add(kv.Key);
                            AddRecentEvent("quest", $"📋 新任务: {kv.Value.title}", Game1.ticks);
                        }
                        else if (kv.Value.completed && !_knownQuestDone.Contains(kv.Key))
                        {
                            _knownQuestDone.Add(kv.Key);
                            AddRecentEvent("quest", $"✅ 任务完成: {kv.Value.title}（可领奖励，用 quest_progress 查）", Game1.ticks);
                        }
                    }
                    foreach (var gone in _knownQuests.Where(k => !questNow.ContainsKey(k)).ToList())
                        _knownQuests.Remove(gone);
                }
            }
            catch { }

            // 3. 拾取检测已由 OnInventoryChanged 精确接管（Player.InventoryChanged →
            //    diff 数量快照 → 价值/星级/属性/描述小新闻）。HUD 文案轮询又脏又不全，
            //    2026-08-05 移除，避免与小新闻重复上报。
        }
        catch { }
    }

    /// <summary>emote 字符串 → 中文/emoji 显示名（SDV 1.6 Farmer.EMOTES 的 emoteString）。</summary>
    internal static string EmoteDisplayName(string emote)
    {
        return emote switch
        {
            "happy" => "😊 开心",
            "sad" => "😢 难过",
            "heart" => "❤️ 爱心",
            "exclamation" => "❗ 惊讶",
            "note" => "🎵 音符",
            "sleep" => "😴 睡觉",
            "game" => "🎮 游戏",
            "question" => "❓ 疑问",
            "x" => "❌ 摇头",
            "pause" => "⏸ 思考",
            "blush" => "😳 脸红",
            "angry" => "😠 生气",
            "yes" => "✅ 点头",
            "no" => "🙅 摇头",
            _ => $"表情({emote})"
        };
    }

    internal void AddRecentEvent(string type, string content, int tick)
    {
        lock (_recentEvents)
        {
            // 去重：相同内容不重复记录
            if (_recentEvents.Count > 0)
            {
                var last = _recentEvents[^1];
                if (last.TryGetValue("content", out var lastContent) && lastContent?.ToString() == content)
                    return;
            }
            _recentEvents.Add(new Dictionary<string, object?>
            {
                ["type"] = type,
                ["content"] = content,
                ["tick"] = tick
            });
            if (_recentEvents.Count > MAX_RECENT_EVENTS)
                _recentEvents.RemoveAt(0);
        }
    }

    // ═══════════════════════════════════════════════════════════════════
    //  拾取小新闻（Player.InventoryChanged 精确检测）
    // ═══════════════════════════════════════════════════════════════════

    private void OnInventoryChanged(object? sender, InventoryChangedEventArgs e)
    {
        if (!Context.IsWorldReady || !e.IsLocalPlayer || !_invCountsInited) return;
        try
        {
            // 1. 算净增（用旧计数）：Added 中非装备、数量变多的才是"新获得"
            var gained = new List<(Item item, int delta)>();
            foreach (var it in e.Added)
            {
                if (it == null || IsEquipItem(it)) continue;
                int oldCount = _invCounts.GetValueOrDefault(ItemKey(it));
                int delta = it.Stack - oldCount;      // 合并进已有堆时 it.Stack=合计，差即净增
                if (delta > 0) gained.Add((it, delta));
            }

            // 2. 更新数量快照（Added/Removed 全算，保持计数准确）
            foreach (var it in e.Removed) if (it != null) UpdateInvCount(it, -it.Stack);
            foreach (var it in e.Added) if (it != null) UpdateInvCount(it, it.Stack);

            // 3. 新获得物品入缓冲（停顿后再落新闻，凑成一批）
            foreach (var (item, delta) in gained)
                AddPendingPickup(item, delta);
        }
        catch (Exception ex)
        {
            Monitor.Log($"OnInventoryChanged error: {ex.Message}", LogLevel.Debug);
        }
    }

    private void AddPendingPickup(Item item, int delta)
    {
        lock (_pickupLock)
        {
            if (_pendingPickups.TryGetValue(ItemKey(item), out var info))
            {
                info.Count += delta;
            }
            else
            {
                _pendingPickups[ItemKey(item)] = new PickupInfo
                {
                    Key = ItemKey(item),
                    DisplayName = SafeDisplayName(item),
                    Quality = (item as StardewValley.Object)?.Quality ?? 0,
                    Value = SafeSellPrice(item),
                    Stats = DescribeItemStats(item),
                    Description = ShortDescription(item),
                    Count = delta
                };
            }
            _lastPickupTick = Game1.ticks;
        }
    }

    private void FlushPickupNews()
    {
        List<PickupInfo> items;
        lock (_pickupLock)
        {
            if (_pendingPickups.Count == 0) return;
            items = _pendingPickups.Values.ToList();
            _pendingPickups.Clear();
        }

        var lines = new List<string>();
        var descQualities = new HashSet<int>();      // 同批内多个同星级物品只插一次文字描述
        foreach (var info in items)
        {
            string q = info.Quality switch { 1 => "[银]", 2 => "[金]", 3 => "[铱]", _ => "" };
            var bits = new List<string> { $"{q}{info.DisplayName}" };
            if (info.Count > 1) bits.Add($"×{info.Count}");
            if (info.Value > 0) bits.Add($"({info.Value}g)");
            if (!string.IsNullOrEmpty(info.Stats)) bits.Add(info.Stats);
            lines.Add("· " + string.Join(" ", bits));   // 不加"获得:"（也没统计失去），省 token
            // 描述：同批同星级一次 + 当天内同(物品|星级)第一次（跨批不重复）
            if (!string.IsNullOrEmpty(info.Description) && descQualities.Add(info.Quality) && _descShownToday.Add(info.Key))
                lines.Add($"  📖 {info.Description}");
        }
        AddRecentEvent("pickup", string.Join("\n", lines), Game1.ticks);
    }

    // ── 穿戴单件新闻：每 15 tick 比对签名，只报变化的槽位 ──

    private void DetectWornChanges()
    {
        if (!Context.IsWorldReady || Game1.player == null) return;
        if (Game1.ticks - _lastWornCheckTick < 15) return;
        _lastWornCheckTick = Game1.ticks;

        var farmer = Game1.player;
        var now = new Dictionary<string, Item?>
        {
            ["shirt"] = farmer.shirtItem?.Value,
            ["pants"] = farmer.pantsItem?.Value,
            ["hat"] = farmer.hat?.Value,
            ["boots"] = farmer.boots?.Value,
            ["leftRing"] = farmer.leftRing?.Value,
            ["rightRing"] = farmer.rightRing?.Value,
            ["trinket"] = farmer.trinketItems?.FirstOrDefault(),
            ["accessory"] = GetAccessoryItem(farmer)
        };
        if (_lastWorn.Count == 0)
        {
            _lastWorn = now.ToDictionary(kv => kv.Key, kv => kv.Value == null ? "" : SafeDisplayName(kv.Value));
            return;   // 首次只记录，不弹
        }

        var parts = new List<string>();
        foreach (var kv in now)
        {
            string newName = kv.Value == null ? "" : SafeDisplayName(kv.Value);
            string oldName = _lastWorn.TryGetValue(kv.Key, out var o) ? o : "";
            if (oldName == newName) continue;
            // 极简箭头式：衬衫→午夜狗T恤（去 emoji/文字，省 token）
            if (kv.Value == null)
                parts.Add($"{oldName}→");              // 脱下
            else if (string.IsNullOrEmpty(oldName))
                parts.Add($"→{newName}");              // 穿上
            else
                parts.Add($"{oldName}→{newName}");     // 换
            // 衣服/饰品也有解释：附一句描述（脱下的没有新物品，不附）
            if (kv.Value != null)
            {
                string desc = ShortDescription(kv.Value);
                if (!string.IsNullOrEmpty(desc))
                    parts.Add($"  📖 {desc}");
            }
        }
        _lastWorn = now.ToDictionary(kv => kv.Key, kv => kv.Value == null ? "" : SafeDisplayName(kv.Value));
        if (parts.Count > 0)
            AddRecentEvent("wear", string.Join("\n", parts), Game1.ticks);
    }

    private Item? GetAccessoryItem(Farmer farmer)
    {
        try
        {
            var f = typeof(Farmer).GetField("accessory",
                BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
            var v = f?.GetValue(farmer);
            if (v is Item direct) return direct;
            return v?.GetType().GetProperty("Value")?.GetValue(v) as Item;
        }
        catch { return null; }
    }

    private void UpdateInvCount(Item it, int amount)
    {
        string k = ItemKey(it);
        _invCounts[k] = Math.Max(0, _invCounts.GetValueOrDefault(k) + amount);
    }

    private static string ItemKey(Item it)
    {
        string id;
        try { id = it.QualifiedItemId ?? it.ItemId; } catch { id = it.ItemId; }
        int q = (it as StardewValley.Object)?.Quality ?? 0;
        return $"{id}|{q}";
    }

    private static bool IsEquipItem(Item i)
    {
        try
        {
            if (i is StardewValley.Objects.Clothing || i is StardewValley.Objects.Hat
                || i is StardewValley.Objects.Boots)
                return true;
            // ⚠️ CombinedRing 不按装备过滤——2026-08-10 恒：锻造合成结果要进小新闻给 AI 看（含组件效果）
            if (i is StardewValley.Objects.Ring && i is not StardewValley.Objects.CombinedRing)
                return true;
            // trinket：引用 DLL 无该类型（1.6 运行时才有），按类型名兜底
            var t = i.GetType();
            return t.Name == "Trinket" || t.FullName?.Contains(".Trinket") == true;
        }
        catch { return false; }
    }

    private static string SafeDisplayName(Item? it)
    {
        try { return it?.DisplayName ?? it?.Name ?? "?"; } catch { return it?.Name ?? "?"; }
    }

    /// <summary>手动加食物 buff：SDV eatObject 程序化调用不加 buff（实测 Spicy Eel 吃后 buffs 空），
    /// 1.6 的 Buff 类编译引用不到，用反射从游戏程序集找 + 反射调 buffsDisplay 加。</summary>
    private static void TryAddFoodBuff(StardewValley.Object obj)
    {
        try
        {
            var flags = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic
                | System.Reflection.BindingFlags.Instance;
            var buffType = typeof(Game1).Assembly.GetType("StardewValley.Buffs.Buff");
            if (buffType == null || Game1.buffsDisplay == null) return;
            var ctor = buffType.GetConstructor(new[] { typeof(string) });
            if (ctor == null) return;
            var buff = ctor.Invoke(new object[] { obj.QualifiedItemId });
            foreach (var mn in new[] { "addOtherBuff", "addBuff", "Add" })
            {
                var m = Game1.buffsDisplay.GetType().GetMethods(flags).FirstOrDefault(x => x.Name == mn);
                if (m != null) { m.Invoke(Game1.buffsDisplay, new object[] { buff }); break; }
            }
        }
        catch { }
    }

    /// <summary>解析地图 object 的显示名：Name 取不到时先按 itemId 映射矿节点（英文名，供脚本关键字匹配），
    /// 再退回 DisplayName，最后兜底裸 id。矿节点等地图 object 的 ItemRegistry 数据缺失时会 Name=null。</summary>
    private static string? SafeObjectName(StardewValley.Object obj)
    {
        // 矿节点：SDV 1.6 Name 报 'Stone'（矿节点与石头同名 "X Stone"），必须用 itemId 区分
        // 实测 1.6 矿节点 itemId：751铜 / 290铁 / 764金 / 765铱 / 767神秘石
        var id = obj.itemId?.Value ?? obj.ParentSheetIndex.ToString();
        var mapped = id switch
        {
            "751" => "Copper Node",
            "290" => "Iron Node",
            "764" => "Gold Node",
            "765" => "Iridium Node",
            "767" => "Mystic Stone",
            _ => null
        };
        if (mapped != null) return mapped;
        try
        {
            var n = obj.Name;
            if (!string.IsNullOrEmpty(n)) return n;
        }
        catch { }
        try
        {
            var dn = obj.DisplayName;
            if (!string.IsNullOrEmpty(dn)) return dn;
        }
        catch { }
        return id;
    }

    private static int SafeSellPrice(Item it)
    {
        try { return Math.Max(0, it.sellToStorePrice()); } catch { return 0; }
    }

    private static string ShortDescription(Item? it)
    {
        try
        {
            var d = (it?.getDescription() ?? "").Replace("\n", " ").Replace("\r", " ").Trim();
            return d.Length > 40 ? d[..40] + "…" : d;
        }
        catch { return ""; }
    }

    private static string DescribeItemStats(Item it)
    {
        try
        {
            switch (it)
            {
                case StardewValley.Objects.CombinedRing cr:
                {
                    // 组合戒指：报组件（两枚戒指效果叠加）——2026-08-10 恒：合成效果要能看到
                    try
                    {
                        var parts = new List<string>();
                        foreach (var r in cr.combinedRings)
                        {
                            if (r != null) parts.Add(r.Name);
                        }
                        return "组合:" + string.Join("+", parts);
                    }
                    catch { return ""; }
                }
                case MeleeWeapon w:
                {
                    var bits = new List<string> { $"攻{w.minDamage.Value}-{w.maxDamage.Value}" };
                    if (w.speed.Value != 0) bits.Add($"速{w.speed.Value:+0;-0}");
                    if (w.critChance.Value > 0f) bits.Add($"暴{w.critChance.Value * 100f:0}%");
                    if (w.knockback.Value > 0f) bits.Add($"击退{w.knockback.Value:0.#}");
                    // ⚠️ 附魔效果（2026-08-10 恒：附魔武器看到效果非常重要）
                    try
                    {
                        if (w.enchantments != null && w.enchantments.Count > 0)
                        {
                            var ench = new List<string>();
                            foreach (var e in w.enchantments)
                            {
                                if (e == null) continue;
                                try { ench.Add(e.GetDisplayName()); }
                                catch { try { ench.Add(e.GetName()); } catch { } }
                            }
                            if (ench.Count > 0) bits.Add("附魔:" + string.Join(",", ench));
                        }
                    }
                    catch { }
                    return string.Join(" ", bits);
                }
                case StardewValley.Objects.Boots b:
                    return $"防{b.defenseBonus.Value}免{b.immunityBonus.Value}";
                case Tool tool:
                {
                    var tbits = new List<string>();
                    if (tool.UpgradeLevel > 0) tbits.Add($"Lv.{tool.UpgradeLevel}");
                    // ⚠️ 工具附魔显示（2026-08-10 恒：附魔武器/工具看到效果非常重要）
                    try
                    {
                        if (tool.enchantments != null && tool.enchantments.Count > 0)
                        {
                            var tench = new List<string>();
                            foreach (var te in tool.enchantments)
                            {
                                if (te == null) continue;
                                try { tench.Add(te.GetDisplayName()); }
                                catch { try { tench.Add(te.GetName()); } catch { } }
                            }
                            if (tench.Count > 0) tbits.Add("附魔:" + string.Join(",", tench));
                        }
                    }
                    catch { }
                    return string.Join(" ", tbits);
                }
                case StardewValley.Object obj when obj.Edibility > 0:
                    return $"恢复{obj.Edibility}体力/{Math.Max(1, (int)(obj.Edibility * 0.45))}血";
                default:
                    return "";
            }
        }
        catch { return ""; }
    }

    private void OnUpdateTicked(object? sender, UpdateTickedEventArgs e)
    {
        _chatHud?.Update();

        // Drain main-thread action queue
        lock (_queueLock)
        {
            while (_mainThreadQueue.Count > 0)
            {
                try { _mainThreadQueue.Dequeue().Invoke(); }
                catch (Exception ex) { Monitor.Log($"Queued action error: {ex}", LogLevel.Error); }
            }
        }

        // farmhand 过夜结算：ShippingMenu（收益结算界面）由 AI 亲自确认关掉（复盘/规划窗口）。
        // 只在超过 SettlementAutoDismissMs 后兜底自动点——AI 挂了/MCP 断了结算界面卡死时防冻。
        // 只对 farmhand(非host)生效——host 小恒的结算界面由真人操作。
        // ⚠️ 不要在这里加 ReadyCheckDialog 自动 confirm：2am 昏迷时 PassOutNewDay 弹的
        // ReadyCheckDialog onConfirm=NewDay(0f)，提前 confirm 会让 farmhand 抢先触发过夜
        // newDaySync，与 host 不同步 → 死锁卡死。ReadyCheckDialog 自身每帧 SetLocalReady +
        // 全员就绪自动 confirm，游戏机制自洽，无需外部干预。
        if (Context.IsWorldReady && Game1.player != null && !Game1.player.IsMainPlayer)
        {
            try
            {
                if (Game1.activeClickableMenu is StardewValley.Menus.ShippingMenu sm)
                {
                    double now = Game1.currentGameTime.TotalGameTime.TotalMilliseconds;
                    if (_settlementShownMs <= 0)
                        _settlementShownMs = now;
                    if (now - _settlementShownMs >= SettlementAutoDismissMs)
                        sm.receiveLeftClick(sm.okButton.bounds.X, sm.okButton.bounds.Y);
                }
                else
                {
                    _settlementShownMs = 0;
                }

                // ⚠️ 2026-08-14 撤除：持续重发 ready + AutoRefresh + SLEEP-DEBUG 日志。
                //    持续重发/AutoRefresh 是为反向一起睡加的 hack；SLEEP-DEBUG 每2秒调 GetNumberReady，
                //    可能在全国同步时阻塞卡死游戏（恒实测干净版重启后 7843冻结/7842未响应）。全撤，回到纯核心。
                //    核心机制：Python go_sleep（walk-out-refresh 内置）+ /sleep 就地睡 + crawl_bed 不挪位。

                // ⚠️ 2026-08-15 恒：LevelUpMenu（升级界面）farmhand 自动确认——
                //    AI 的 menu/click 坐标系统不匹配关不掉，卡死。只在非职业选择(isProfessionChooser=false)
                //    时自动点 OK（5/10级职业选择留给 AI 决策）。
                if (Game1.activeClickableMenu is StardewValley.Menus.LevelUpMenu lum
                    && lum.okButton != null && !lum.isProfessionChooser)
                {
                    lum.receiveLeftClick(lum.okButton.bounds.Center.X, lum.okButton.bounds.Center.Y);
                    // 🎉 播报升级给 AI（技能+等级用反射读——LevelUpMenu 字段非公开，2026-08-15 恒：
                    //    自动确认不能让 AI 蒙在鼓里）
                    try
                    {
                        int newLevel = 0;
                        var lf = typeof(StardewValley.Menus.LevelUpMenu)
                            .GetField("currentLevel", System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
                        if (lf?.GetValue(lum) is int lv) newLevel = lv;
                        EnqueueAlert("levelup", $"🎉 升级了！(新等级 {newLevel})", "info", "levelup");
                    }
                    catch { }
                }
            }
            catch (Exception ex)
            {
                Monitor.Log($"farmhand 结算自动确认错误: {ex.Message}", LogLevel.Warn);
            }
        }

        // 静止计时：玩家 TilePoint 未变的真实秒数（发呆检测用）
        if (Context.IsWorldReady && Game1.player != null)
        {
            // 数量快照初始化（世界就绪时打底，供拾取 diff 用）
            if (!_invCountsInited)
            {
                try
                {
                    _invCounts.Clear();
                    foreach (var it in Game1.player.Items)
                        if (it != null) UpdateInvCount(it, it.Stack);
                    _invCountsInited = true;
                }
                catch { }
            }

            // 天界重置：小新闻账本按天清空 + 描述去重按天清（"看过即清空"之外兜底）
            string dayKey = $"{Game1.currentSeason}|{Game1.dayOfMonth}|{Game1.year}";
            if (_lastDayKey != dayKey)
            {
                bool firstInit = _lastDayKey == null;
                _lastDayKey = dayKey;
                if (!firstInit)
                {
                    lock (_recentEvents) _recentEvents.Clear();
                    lock (_pickupLock) _pendingPickups.Clear();
                    _lastPickupTick = -1;
                    _descShownToday.Clear();
                }
            }

            try
            {
                var tile = Game1.player.TilePoint;
                double now = Game1.currentGameTime.TotalGameTime.TotalSeconds;
                if (_lastTilePoint == null || _lastTilePoint.Value != tile)
                {
                    _lastTilePoint = tile;
                    _lastTileChangeSeconds = now;
                }
                _stationarySeconds = now - _lastTileChangeSeconds;
            }
            catch { }
        }

        // Freeze time if paused
        if (_timeFrozen && Context.IsWorldReady)
            Game1.timeOfDay = _frozenTime;

        if (Context.IsWorldReady && Game1.player != null)
        {
            CaptureAlerts();
            FlushPickupNews();      // 拾取缓冲停顿后落小新闻
            DetectWornChanges();    // 穿戴变化单件小新闻
        }

        // --- Walk-to multi-map routing ---
        // Detects map transitions (player walked onto warp → game auto-transitions)
        // and advances to the next route segment automatically.
        if (_walkRoute != null && Context.IsWorldReady && Game1.player != null)
        {
            var farmer = Game1.player;
            var seg = _walkRoute[_walkSegIdx];

            // ⚠️ 2026-08-14：地点名比较用 NameOrUniqueName（真实名）。小屋 Name="Cabin" 但真实名
            //    "FarmHouse<guid>"，用 Name 比较永远不等 → 以为没到目标地图 → warpPending 不清 → 在门口傻等不走。
            var curLocName = farmer.currentLocation?.NameOrUniqueName ?? farmer.currentLocation?.Name;
            if (curLocName != seg.Location)
            {
                // Warp is still pending → wait for it to land on the target map
                if (_warpPending)
                    return;
                // Natural warp (player walked onto exit) → advance segment
                _walkSegIdx++;
                _walkSegmentStarted = false;
                _pathQueue = null;
                if (_walkSegIdx >= _walkRoute.Count)
                {
                    _walkRoute = null;
                    _walkSegIdx = 0;
                }
                EnqueueAlert("walk_segment", $"Entered {farmer.currentLocation.Name}", "info", "walk");
                return; // let next tick start the new segment
            }
            // Warp completed (same map now) → clear flag so BFS can start
            _warpPending = false;

            if ((_pathQueue == null || _pathQueue.Count == 0) && !_walkSegmentStarted)
            {
                // Start walking to this segment's target tile
                _walkSegmentStarted = true;
                var start = farmer.TilePoint;
                var target = new Point(seg.TargetX, seg.TargetY);
                var path = FindPath(farmer.currentLocation, start, target);

                // If the exact warp tile isn't reachable (e.g. door tiles),
                // try walking to the nearest adjacent passable tile instead.
                if (path == null && _walkSegIdx < _walkRoute.Count - 1)
                {
                    int[] dx = { 0, 0, -1, 1 };
                    int[] dy = { -1, 1, 0, 0 };
                    foreach (var dir in Enumerable.Range(0, 4).OrderBy(d => Math.Abs(dx[d]) + Math.Abs(dy[d])))
                    {
                        var adj = new Point(target.X + dx[dir], target.Y + dy[dir]);
                        if (adj == start) { path = new Queue<Point>(); break; }
                        path = FindPath(farmer.currentLocation, start, adj);
                        if (path != null) break;
                    }
                }

                _pathQueue = path; // null = no reachable path, movement won't start
                _pathTickCooldown = 0;
                if (path == null || path.Count == 0)
                {
                    // BFS couldn't find a path → teleport directly to destination as fallback
                    EnqueueAlert("walk_teleport", $"BFS failed, teleporting to ({seg.TargetX},{seg.TargetY})", "warning", "walk");
                    var targetPos = new Vector2(seg.TargetX, seg.TargetY) * Game1.tileSize;
                    farmer.Position = targetPos;
                    // Also clear the route
                    _walkRoute = null;
                    _walkSegIdx = 0;
                    _walkSegmentStarted = false;
                    _pathQueue = null;
                    EnqueueAlert("walk_completed", $"Teleported to {seg.Location} ({seg.TargetX},{seg.TargetY})", "info", "walk");
                }
            }
            else if ((_pathQueue == null || _pathQueue.Count == 0) && _walkSegmentStarted
                     && _walkSegIdx >= _walkRoute.Count - 1)
            {
                // BFS finished on the final segment → all done
                _walkRoute = null;
                _walkSegIdx = 0;
                _walkSegmentStarted = false;
                EnqueueAlert("walk_completed", $"Arrived at {farmer.currentLocation.Name} ({farmer.TilePoint.X},{farmer.TilePoint.Y})", "info", "walk");
            }
            else if ((_pathQueue == null || _pathQueue.Count == 0) && _walkSegmentStarted
                     && _walkSegIdx < _walkRoute.Count - 1)
            {
                // BFS finished on an intermediate segment — arrived at/near a warp exit tile.
                // Force the warp to the next map.
                var tile = farmer.TilePoint;
                var warp = farmer.currentLocation?.warps?.FirstOrDefault(w => w.X == tile.X && w.Y == tile.Y)
                    // If not exactly on the warp, check adjacent tiles (building doorways)
                    ?? farmer.currentLocation?.warps?.FirstOrDefault(w =>
                        Math.Abs(w.X - tile.X) <= 1 && Math.Abs(w.Y - tile.Y) <= 1);
                if (warp != null && !_warpPending)
                {
                    _warpPending = true;
                    EnqueueAlert("walk_segment", $"Warping: {seg.Location} → {warp.TargetName}", "info", "walk");
                    EnqueueMainThread(() =>
                    {
                        Game1.warpFarmer(warp.TargetName, warp.TargetX, warp.TargetY, false);
                    });
                    // Don't increment _walkSegIdx here — let the location-change
                    // detection (first if) advance the route after the warp actually happens.
                    // Don't reset _walkSegmentStarted either — it prevents re-entering this block.
                    _pathQueue = null;
                }
            }
            else if (_walkSegIdx < _walkRoute.Count)
            {
                // (debug: no-op)
            }
        }

        // Process pathfinding movement
        if (_pathQueue != null && _pathQueue.Count > 0 && Context.IsWorldReady)
        {
            if (_pathTickCooldown > 0)
            {
                _pathTickCooldown--;
                return;
            }

            var next = _pathQueue.Peek();
            var farmer = Game1.player;
            var target = new Vector2(next.X * 64 + 32, next.Y * 64 + 32);
            var diff = target - farmer.Position;

            if (diff.Length() < 6f)
            {
                _pathQueue.Dequeue();
                _pathTickCooldown = 0;
            }
            else
            {
                // Set facing direction
                if (Math.Abs(diff.X) > Math.Abs(diff.Y))
                    farmer.FacingDirection = diff.X > 0 ? 1 : 3;
                else
                    farmer.FacingDirection = diff.Y > 0 ? 2 : 0;

                var speed = farmer.getMovementSpeed();
                if (diff.Length() < speed)
                    farmer.Position = target;
                else
                {
                    diff.Normalize();
                    farmer.Position += diff * speed;
                }
            }
        }

        // Manual warp trigger: when standing on a warp tile, force the transition.
        // Needed because the direct position manipulation bypasses the game's natural warp detection.
        // Skip during active walk_to routing — walk_to's own logic handles warp transitions.
        if (_walkRoute == null && (_pathQueue == null || _pathQueue.Count == 0))
        {
            if (Context.IsWorldReady && Game1.player != null && !Game1.player.isInBed.Value)
            {
                var farmer = Game1.player;
                var tile = farmer.TilePoint;
                var warp = farmer.currentLocation?.warps?.FirstOrDefault(w => w.X == tile.X && w.Y == tile.Y);
                if (warp != null)
                {
                    var (wloc, wx, wy) = ResolveWarp(warp.TargetName, warp.TargetX, warp.TargetY);
                    EnqueueMainThread(() =>
                    {
                        Game1.warpFarmer(wloc, wx, wy, false);
                    });
                    EnqueueAlert("warp", $"Warped to {wloc} ({wx},{wy})", "info", "bridge");
                }
            }
        }

        // Tool charging — visual delay then direct tile modification
        if (_isChargingTool && Context.IsWorldReady)
        {
            if (_toolChargeTicks > 0)
            {
                _toolChargeTicks--;
                if (_toolChargeTicks == 0)
                {
                    _isChargingTool = false;
                    var farmer = Game1.player;
                    if (farmer?.CurrentTool is Tool tool && _chargeOp != null)
                    {
                        var loc = farmer.currentLocation;
                        var ft = farmer.TilePoint;
                        // ⚠️ 还原真工具蓄力（2026-08-13）：DoFunction 真挥锄
                        //   （游戏算真实地块+挥锄动画+自动尊重 Diggable；不再用自定义 GetToolAffectedTiles 几何）
                        try
                        {
                            var facingTile = GetFacingTile(farmer);
                            int px = (int)facingTile.X * 64 + 32;
                            int py = (int)facingTile.Y * 64 + 32;
                            if (_chargeOp == "till" && tool is Hoe hoe)
                                hoe.DoFunction(loc, px, py, _chargePower, farmer);
                            else if (_chargeOp == "water" && tool is WateringCan wc)
                                wc.DoFunction(loc, px, py, _chargePower, farmer);
                            farmer.EndUsingTool();
                        }
                        catch (Exception ex)
                        {
                            farmer.EndUsingTool();
                        }

                        // ⚠️ 2026-08-15 删：不再直接改地块兜底（会"判两次"+作弊——GetToolAffectedTiles 与 DoFunction
                        //    覆盖不同/漂移时，把已浇的格也重浇 → 铜壶 3+1=4 格）。
                        //    漏格由逐锚点验证 VerifyToolAreaAnchor 当场补（position+DoFunction 真补），不直接改 state。
                        var tiles = GetToolAffectedTiles(ft.X, ft.Y, farmer.FacingDirection, _chargePower);
                        _commandResults.Add(new
                        {
                            ok = true, action = "charge_release", tool = tool.Name,
                            power = _chargePower, tiles = tiles.Count, affected = tiles.Count, dofunction = true
                        });
                    }
                    _commandDelay = 3;
                    // Check if queue is fully processed after this charge release
                    if (_commandQueue == null || _commandQueue.Count == 0)
                        CompleteCommandQueue();
                }
            }
            return;
        }

        // Process command queue
        if (_commandQueue != null && _commandQueue.Count > 0 && Context.IsWorldReady)
        {
            // Wait for delay between commands
            if (_commandDelay > 0)
            {
                _commandDelay--;
                return;
            }

            // Wait for move to complete before next command
            if (_waitingForMove)
            {
                if (_pathQueue != null && _pathQueue.Count > 0)
                    return; // still walking
                _waitingForMove = false;
                // ⚠️ 2026-08-16 恒：浇水到锚点后多停一拍（基础壶逐格，别接着就挥）
                _commandDelay = _toolAreaOperation == "water" ? 10 : 5; // small gap after arriving
                return;
            }

            // Wait for fish bite
            if (_waitingForBite)
            {
                _biteTimeout--;
                if (_biteTimeout <= 0)
                {
                    _waitingForBite = false;
                    _commandResults.Add(new { ok = false, action = "wait_for_bite", error = "Timed out waiting for bite" });
                    // Don't abort queue - let next commands handle it
                }
                else if (Game1.player.CurrentTool is FishingRod fishRod && fishRod.isNibbling)
                {
                    _waitingForBite = false;
                    _commandResults.Add(new { ok = true, action = "wait_for_bite", message = "Fish is biting!" });
                    _commandDelay = 2; // tiny delay before reeling
                }
                else
                    return; // keep waiting
                return;
            }

            var cmd = _commandQueue.Dequeue();
            var action = CmdString(cmd, "action");

            try
            {
                switch (action)
                {
                    case "move":
                    {
                        var x = CmdInt(cmd, "x");
                        var y = CmdInt(cmd, "y");
                        var farmer = Game1.player;
                        // 关菜单（实测：菜单开着角色走不动，是 move 卡住根因）
                        if (Game1.activeClickableMenu != null)
                        {
                            try { Game1.activeClickableMenu.exitThisMenu(); } catch { }
                            Game1.activeClickableMenu = null;
                        }
                        var path = FindPath(farmer.currentLocation, farmer.TilePoint, new Point(x, y));
                        if (path == null || path.Count == 0)
                        {
                            // BFS 失败 → 瞬移保底
                            ClearMovementState();
                            farmer.Position = new Vector2(x, y) * Game1.tileSize;
                            CenterViewportOnFarmer(farmer);
                            _waitingForMove = false;
                            _commandResults.Add(new { ok = true, action = "move", x, y, teleported = true });
                        }
                        else
                        {
                            _pathQueue = path;
                            _pathTickCooldown = 0;
                            _waitingForMove = true;
                            _commandResults.Add(new { ok = true, action = "move", x, y, steps = path.Count });
                        }
                        _commandDelay = 3;
                        break;
                    }
                    case "face":
                    {
                        var dir = CmdInt(cmd, "direction", 2);
                        Game1.player.FacingDirection = dir;
                        _commandResults.Add(new { ok = true, action = "face", direction = dir });
                        _commandDelay = 3;
                        break;
                    }
                    case "select":
                    {
                        var name = CmdString(cmd, "name");
                        var farmer = Game1.player;
                        var idx = -1;
                        for (int i = 0; i < farmer.Items.Count; i++)
                        {
                            if (farmer.Items[i] != null && farmer.Items[i].Name.Equals(name, StringComparison.OrdinalIgnoreCase))
                            { idx = i; break; }
                        }
                        if (idx >= 0)
                        {
                            farmer.CurrentToolIndex = idx;
                            _commandResults.Add(new { ok = true, action = "select", name, slot = idx });
                        }
                        else
                            _commandResults.Add(new { ok = false, action = "select", error = $"Item '{name}' not found" });
                        _commandDelay = 3;
                        break;
                    }
                    case "use":
                    {
                        var farmer = Game1.player;
                        var item = farmer.CurrentItem;
                        if (item is WateringCan wcUse)
                        {
                            // ⚠️ 2026-08-15 隔离基础工具：BeginUsingTool（挥舞动画，拟人）+ DoFunction（立即落地）都调——
                            //    只 DoFunction 会没动画像"漂移"；只 BeginUsingTool 会动画没走完就飞（漏格）。
                            //    两个一起：动画显示 + 可靠应用（重复应用是 no-op，无害）。
                            farmer.BeginUsingTool();
                            var ft = GetFacingTile(farmer);
                            wcUse.DoFunction(farmer.currentLocation, (int)ft.X * 64 + 32, (int)ft.Y * 64 + 32, 0, farmer);
                            _commandResults.Add(new { ok = true, action = "use", item = item.Name });
                        }
                        else if (item is Hoe hoeUse)
                        {
                            farmer.BeginUsingTool();
                            var ft = GetFacingTile(farmer);
                            hoeUse.DoFunction(farmer.currentLocation, (int)ft.X * 64 + 32, (int)ft.Y * 64 + 32, 0, farmer);
                            _commandResults.Add(new { ok = true, action = "use", item = item.Name });
                        }
                        else if (item is Tool)
                        {
                            farmer.BeginUsingTool();
                            _commandResults.Add(new { ok = true, action = "use", item = item.Name });
                        }
                        else if (item is StardewValley.Object obj)
                        {
                            var facingTile = GetFacingTile(farmer);
                            int px = (int)facingTile.X * 64;
                            int py = (int)facingTile.Y * 64;
                            bool placed = obj.placementAction(farmer.currentLocation, px, py, farmer);
                            if (placed)
                            {
                                farmer.reduceActiveItemByOne();
                                _commandResults.Add(new { ok = true, action = "placed", item = item.Name });
                            }
                            else
                                _commandResults.Add(new { ok = false, action = "use", error = $"Cannot use '{item.Name}' here" });
                        }
                        else
                            _commandResults.Add(new { ok = false, action = "use", error = "No usable item" });
                        // ⚠️ 2026-08-16 恒：工具节奏——浇水40 tick(人类感✓)，锄地30(稍快些)。
                        //    基础工具挥后多停（效果落地明显再移下格），别边做边移。
                        _commandDelay = _toolAreaOperation == "water" ? 40 : 30;
                        break;
                    }
                    case "charge":
                    {
                        var farmer = Game1.player;
                        if (farmer?.CurrentTool is Tool tool)
                        {
                            int power = CmdInt(cmd, "power", -1);
                            if (power < 0) power = tool.UpgradeLevel;
                            int frames = CmdInt(cmd, "frames", 20);

                            // Visual charge delay — tile modification happens later when timer hits 0
                            _isChargingTool = true;
                            _toolChargeTicks = frames;
                            _chargePower = power;
                            _chargeOp = tool is Hoe ? "till" : tool is WateringCan ? "water" : null;
                            // ⚠️ 还原真蓄力（2026-08-13）：BeginUsingTool 出蓄力姿势。
                            //   farmer.toolPower 是 readonly 不能设（SDV1.6），形状由锄头 UpgradeLevel 决定，DoFunction power 参数只是参考
                            try { farmer.BeginUsingTool(); } catch { }

                            _commandResults.Add(new
                            {
                                ok = true, action = "charge_start", tool = tool.Name,
                                power, frames
                            });
                        }
                        else
                            _commandResults.Add(new { ok = false, action = "charge", error = "No tool selected" });
                        break;
                    }
                    case "interact":
                    {
                        var farmer = Game1.player;
                        var facingTile = GetFacingTile(farmer);
                        var acted = farmer.currentLocation.checkAction(
                            new Location((int)facingTile.X, (int)facingTile.Y), Game1.viewport, farmer);
                        _commandResults.Add(new { ok = true, action = "interact", triggered = acted });
                        _commandDelay = 10;
                        break;
                    }
                    case "wait":
                    {
                        var ticks = CmdInt(cmd, "ticks", 60);
                        _commandResults.Add(new { ok = true, action = "wait", ticks });
                        _commandDelay = ticks;
                        break;
                    }
                    case "warp":
                    {
                        var loc = CmdString(cmd, "location");
                        var wx = CmdInt(cmd, "x", 10);
                        var wy = CmdInt(cmd, "y", 10);
                        Game1.warpFarmer(loc, wx, wy, false);
                        _commandResults.Add(new { ok = true, action = "warp", location = loc, x = wx, y = wy });
                        _commandDelay = 30; // wait for warp to complete
                        break;
                    }
                    case "wait_for_bite":
                    {
                        var timeout = CmdInt(cmd, "timeout", 1800);
                        _waitingForBite = true;
                        _biteTimeout = timeout;
                        break;
                    }
                    case "key":
                    {
                        var keyName = CmdString(cmd, "key", "confirm");
                        switch (keyName.ToLower())
                        {
                            case "confirm": case "action":
                                Game1.pressActionButton(Game1.input.GetKeyboardState(), Game1.input.GetMouseState(), Game1.input.GetGamePadState());
                                break;
                            case "skip": case "escape":
                                if (Game1.activeClickableMenu != null)
                                    Game1.activeClickableMenu.receiveKeyPress(Keys.Escape);
                                else
                                    Game1.activeClickableMenu?.exitThisMenu();
                                break;
                        }
                        _commandResults.Add(new { ok = true, action = "key", key = keyName });
                        _commandDelay = 10;
                        break;
                    }
                    default:
                        _commandResults.Add(new { ok = false, action, error = "Unknown action" });
                        break;
                }
            }
            catch (Exception ex)
            {
                _commandResults.Add(new { ok = false, action, error = ex.Message });
            }

            // 逐锚点验证"落地"（2026-08-15 恒：挥完检查覆盖格状态，漏的当场补漏，再走下一锚点——
            //    覆盖基础/铜/铁/金/铱所有等级，防"没落地就飞下一格"）
            if (action == "use" || action == "charge")
                VerifyToolAreaAnchor(cmd);

            // All commands done? Return results
            if (_commandQueue.Count == 0 && !_isChargingTool)
            {
                CompleteCommandQueue();
            }
        }
    }

    private static bool IsPortAvailable(int port)
    {
        try
        {
            var tcp = new TcpListener(IPAddress.Loopback, port);
            tcp.Start();
            tcp.Stop();
            return true;
        }
        catch { return false; }
    }

    private void StartServer()
    {
        _cts = new CancellationTokenSource();
        var token = _cts.Token;

        Task.Run(async () =>
        {
            // Auto-detect available port starting from 7842
            _listener = null;
            for (_port = 7842; _port < 7850; _port++)
            {
                try
                {
                    var listener = new HttpListener();
                    listener.Prefixes.Add($"http://localhost:{_port}/");
                    listener.Start();
                    _listener = listener;
                    Monitor.Log($"NagiBridge HTTP server started on port {_port}", LogLevel.Info);
                    break;
                }
                catch
                {
                    Monitor.Log($"Port {_port} unavailable, trying next...", LogLevel.Debug);
                }
            }

            if (_listener == null)
            {
                Monitor.Log("Failed to start HTTP server on any port (7842-7849)", LogLevel.Error);
                return;
            }

            while (!token.IsCancellationRequested)
            {
                try
                {
                    var ctx = await _listener.GetContextAsync().ConfigureAwait(false);
                    _ = Task.Run(() => HandleRequest(ctx), token);
                }
                catch (ObjectDisposedException) { break; }
                catch (Exception ex)
                {
                    Monitor.Log($"Listener error: {ex.Message}", LogLevel.Warn);
                }
            }
        }, token);
    }

    private void HandleRequest(HttpListenerContext ctx)
    {
        var path = ctx.Request.Url?.AbsolutePath ?? "/";
        var method = ctx.Request.HttpMethod;

        try
        {
            object? result = path switch
            {
                "/status" => HandleStatus(),
                "/move" => HandleMove(ctx),
                "/tool" => HandleTool(ctx),
                "/weapon_diag" => HandleWeaponDiag(),
                "/interact" => HandleInteract(ctx),
                "/furniture_pickup" => HandleFurniturePickup(ctx),
                "/furniture" => HandleFurniture(ctx),
                "/passable" => HandlePassable(ctx),
                "/chat" => HandleChat(ctx),
                "/emote" => HandleEmote(ctx),
                "/state" => HandleState(ctx),
                "/surroundings" => HandleSurroundings(ctx),
                "/warps" => HandleWarps(),
                "/farm_buildings" => HandleFarmBuildings(),
                "/fish_pond" => HandleFishPond(ctx),
                "/find_npc" => HandleFindNpc(ctx),
                "/trinkets" => HandleTrinkets(),
                "/trinket" => HandleTrinketEquip(ctx),
                "/rings" => HandleRings(),
                "/ring" => HandleRingEquip(ctx),
                "/equip" => HandleEquip(ctx),
                "/worn" => HandleWorn(),
                "/alerts" => HandleAlerts(ctx),
                "/appearance" => HandleAppearance(ctx),
                "/appearance_info" => HandleAppearanceInfo(),
                "/appearance_ref" => HandleAppearanceRef(),
                "/character_customize" => HandleCharacterCustomize(ctx),
                "/color_pick" => HandleColorPick(ctx),
                "/stop" => HandleStop(),
                "/map" => HandleMap(),
                "/buy" => HandleBuy(ctx),
                "/face" => HandleFace(ctx),
                "/select" => HandleSelect(ctx),
                "/use" => HandleUse(ctx),
                "/sleep" => HandleSleep(ctx),
                "/wakeup" => HandleWakeup(),
                "/queue" => HandleQueue(ctx),
                "/key" => HandleKey(ctx),
                "/minigame_click" => HandleMinigameClick(ctx),
                "/minigame_state" => HandleMinigameState(),
                "/focus" => HandleFocus(),
                "/eat" => HandleEat(),
                "/buffs" => HandleBuffs(),
                "/set_pause" => HandleSetPause(ctx),
                "/warp" => HandleWarp(ctx),
                "/warp_into" => HandleWarpInto(ctx),
                "/warp_building" => HandleWarpBuilding(ctx),
                "/walk_to" => HandleWalkTo(ctx),
                "/position" => HandlePosition(ctx),
                "/pause" => HandlePause(),
                "/resume" => HandleResume(),
                "/give" => HandleGive(ctx),
                "/drop" => HandleDrop(ctx),
                "/gift" => HandleGift(ctx),
                "/friendship" => HandleFriendship(ctx),
                "/money" => HandleMoney(ctx),
                "/refill" => HandleRefill(),
                "/heal" => HandleHeal(),
                "/ripen" => HandleRipen(ctx),
                "/sell" => HandleSell(ctx),
                "/sell_to_shop" => HandleSellToShop(ctx),
                "/harvest" => HandleHarvest(ctx),
                "/store" => HandleStore(ctx),
                "/store_all" => HandleStoreAll(ctx),
                "/name_chest" => HandleNameChest(ctx),
                "/chest" => HandleChest(ctx),
                "/chest_take" => HandleChestTake(ctx),
                "/scan_chests" => HandleScanChests(),
                "/placechest" => HandlePlaceChest(ctx),
                "/fishbot" => HandleFishbot(ctx),
                "/menu" => HandleMenu(),
                "/menu/click" => HandleMenuClick(ctx),
                "/menu/claim_swap" => HandleClaimSwap(ctx),
                "/menu_close" => HandleMenuClose(),
                "/forge_set" => HandleForgeSet(ctx),
                "/dump_tile" => HandleDumpTile(ctx),
                "/mine_rock" => HandleMineRock(),   // 🧱 矮人商店堵路石（(BC)78 在 Mine(27,8)）是否还在=未炸（cross-map 读，2026-08-23 恒）
                "/water" => HandleWater(ctx),
                "/click" => HandleClick(ctx),
                "/click_tile" => HandleClickTile(ctx),
                "/drag" => HandleDrag(ctx),
                "/craft" => HandleCraft(ctx),
                "/cook" => HandleCook(ctx),
                "/recipes" => HandleRecipes(),
                "/craft_recipes" => HandleCraftRecipes(),   // 已学合成配方清单（技能等级规则，2026-08-13）
                "/process_geode" => HandleProcessGeode(ctx),
                "/process_geode_batch" => HandleProcessGeodeBatch(ctx),
                "/museum_donate" => HandleMuseumDonate(),
                "/museum_debug" => HandleMuseumDebug(),
                "/museum_tiles" => HandleMuseumTiles(),
                "/museum_diag" => HandleMuseumDiag(),
                "/quest_list" => HandleQuestList(),
                "/quest_progress" => HandleQuestProgress(),
                // ⛔ 2026-08-22 /quest_accept 已退役：接单改走板上 menu click(button=accept…)（可靠 UI 路径，子目标会初始化）。
                //    此端点经 /accept_quest 走数据层直搬 available→specialOrders，未触发 SpecialOrder 初始化，弃用。
                //    下次重编 DLL 随手删 HandleQuestAccept 即可（现注释路由使其不再暴露）。
                // "/quest_accept" => HandleQuestAccept(ctx),
                "/qi_shop" => HandleQiShop(),
                "/qi_buy" => HandleQiBuy(ctx),
                "/museum_remove" => HandleMuseumRemove(ctx),
                "/clear_ground" => HandleClearGround(ctx),
                "/debris" => HandleDebris(),
                "/machines" => HandleMachines(),
                "/farm_report" => HandleFarmReport(),
                "/machine_collect" => HandleMachineCollect(ctx),
                "/machine_load" => HandleMachineLoad(ctx),
                "/animals" => HandleAnimals(),
                "/scan" => HandleScan(),
                "/petbowl" => HandlePetBowl(),
                "/petall" => HandlePetAll(),
                "/waterbowl" => HandleWaterBowl(),
                "/ladder" => HandleLadder(),
                "/silo" => HandleSilo(),
                "/mastery" => HandleMastery(),
                "/special_items" => HandleSpecialItems(),   // 💼 钱包特殊物品列表（含 mastery_xxx/TownKey，2026-08-23 恒）
                "/achievements" => HandleAchievementProbe(),   // 🏆 成就列表（读 player.achievements NetIntHashSet + 成就名，2026-08-23 恒）
                "/mastery_claim" => HandleMasteryClaim(ctx),
                "/carpenter" => HandleCarpenter(),
                "/mine_debug" => HandleMineDebug(),
                "/mine/elevator" => HandleMineElevator(),   // 🪜 读鹈鹕镇矿井电梯当前可达楼层（动态起始层，2026-08-22）
                "/festival" => HandleFestival(),
                "/festival_data" => HandleFestivalData(ctx),   // 📜 节日事件数据转储（反编译蛋蛋位置等，2026-08-17）
                "/egg_tiles" => HandleEggTiles(ctx),           // 🥚 扫地图 Paths 图层 fest* 图块 = 蛋蛋坐标（2026-08-17）
                "/festival/interact" => HandleFestivalInteract(ctx),
                "/festival/answer" => HandleFestivalAnswer(ctx),
                "/dance_invite" => HandleDanceInvite(ctx),   // 💃 花舞节邀请跳舞（2026-08-20 绕过farmhand端buggy对话框）
                "/unlocks" => HandleUnlocks(),
                "/unlock_debug" => HandleUnlockDebug(),
                "/chat/push" => HandleChatPush(ctx),
                "/chat/history" => HandleChatHistory(),
                "/mail" => HandleMail(),   // 📬 读邮箱未读邮件（2026-08-15恒：AI要看信）
                "/hud" => HandleHud(ctx),   // 💬 HUD通知（Game1.addHUDMessage）——⚠️ 2026-08-15恒：不再用于推送给user（过夜复盘看不到），统一走/chat
                "/buy_animal" => HandleBuyAnimal(ctx),
                "/sprinklers" => HandleSprinklers(),
                "/till_area" => HandleTillArea(ctx),
                "/tool_area" => HandleToolArea(ctx),
                "/dig_spot" => HandleDigSpot(ctx),   // 🪱 挖蚯蚓格子/姜（digUpArtifactSpot，2026-08-17）
                "/toggle_doors" => HandleToggleDoors(),
                "/crawl_bed" => HandleCrawlBed(ctx),
                "/cancel_sleep" => HandleCancelSleep(),
                "/settlement_confirm" => HandleSettlementConfirm(),
                "/ready_state" => HandleReadyState(),
                "/screenshot" => HandleScreenshot(),
                "/zoom" => HandleZoom(ctx),
                "/resolution" => HandleResolution(ctx),
                _ => throw new InvalidOperationException($"Unknown endpoint: {path}")
            };

            Respond(ctx, 200, result ?? new { ok = true });
        }
        catch (Exception ex)
        {
            Respond(ctx, 400, new { error = ex.Message });
        }
    }

    private static void Respond(HttpListenerContext ctx, int status, object body)
    {
        var json = JsonSerializer.Serialize(body, new JsonSerializerOptions { WriteIndented = false });
        var buf = Encoding.UTF8.GetBytes(json);
        ctx.Response.StatusCode = status;
        ctx.Response.ContentType = "application/json; charset=utf-8";
        ctx.Response.ContentLength64 = buf.Length;
        ctx.Response.Headers.Add("Access-Control-Allow-Origin", "*");
        ctx.Response.OutputStream.Write(buf, 0, buf.Length);
        ctx.Response.Close();
    }

    private Dictionary<string, object?> ReadJson(HttpListenerContext ctx)
    {
        using var reader = new StreamReader(ctx.Request.InputStream, Encoding.UTF8);
        var body = reader.ReadToEnd();
        if (string.IsNullOrWhiteSpace(body))
            return new Dictionary<string, object?>();
        return JsonSerializer.Deserialize<Dictionary<string, object?>>(body) ?? new();
    }

    private T GetParam<T>(Dictionary<string, object?> dict, string key)
    {
        if (!dict.TryGetValue(key, out var val) || val == null)
            throw new InvalidOperationException($"Missing parameter: {key}");

        if (val is JsonElement je)
        {
            if (typeof(T) == typeof(int)) return (T)(object)je.GetInt32();
            if (typeof(T) == typeof(float)) return (T)(object)je.GetSingle();
            if (typeof(T) == typeof(double)) return (T)(object)je.GetDouble();
            if (typeof(T) == typeof(string)) return (T)(object)(je.GetString() ?? "");
            if (typeof(T) == typeof(bool)) return (T)(object)je.GetBoolean();
        }

        return (T)Convert.ChangeType(val, typeof(T));
    }

    private T GetParamOr<T>(Dictionary<string, object?> dict, string key, T defaultValue)
    {
        if (!dict.TryGetValue(key, out var val) || val == null)
            return defaultValue;

        if (val is JsonElement je)
        {
            if (typeof(T) == typeof(int)) return (T)(object)je.GetInt32();
            if (typeof(T) == typeof(float)) return (T)(object)je.GetSingle();
            if (typeof(T) == typeof(double)) return (T)(object)je.GetDouble();
            if (typeof(T) == typeof(string)) return (T)(object)(je.GetString() ?? "");
            if (typeof(T) == typeof(bool)) return (T)(object)je.GetBoolean();
        }

        return (T)Convert.ChangeType(val, typeof(T));
    }

    // --- Handlers ---

    private object HandleStatus()
    {
        return new
        {
            ok = true,
            server = "NagiBridge",
            version = "1.0.13",
            build = BuildStamp,   // 防倒退：/status 报构建标记，旧 DLL/原作者版会显示不同/无此字段
            port = _port,
            worldReady = Context.IsWorldReady,
            isMultiplayer = Context.IsMultiplayer
        };
    }

    /// <summary>
    /// POST /move  { "x": 10, "y": 15 }
    /// Walks to tile (x, y) using simple straight-line pathfinding.
    /// </summary>
    private object HandleMove(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var tx = GetParam<int>(p, "x");
        var ty = GetParam<int>(p, "y");

        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        // Build simple path: current tile -> target tile (straight line, then adjust)
        var tcs = new TaskCompletionSource<object>();

        EnqueueMainThread(() =>
        {
            var farmer = Game1.player;
            var startTile = farmer.TilePoint;
            var path = FindPath(farmer.currentLocation, startTile, new Point(tx, ty));

            if (path == null || path.Count == 0)
            {
                _pathQueue = null;
                tcs.SetResult(new { ok = false, error = $"No path from ({startTile.X},{startTile.Y}) to ({tx},{ty})" });
                return;
            }
            else
            {
                _pathQueue = path;
            }
            _pathTickCooldown = 0;

            tcs.SetResult(new { ok = true, message = $"Moving to ({tx},{ty}), steps={_pathQueue.Count}" });
        });

        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// POST /tool  { "name": "Axe" } or { "name": "current" }
    /// Swings the specified tool (or current tool) once.
    /// </summary>
    private object HandleTool(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var name = GetParamOr(p, "name", "current");
        var power = GetParamOr(p, "power", -1); // -1=default(0), 0+=charge level
        var special = GetParamOr(p, "special", false); // true=武器蓄力特殊攻击（锤重砸/剑旋风/匕首冲刺）

        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();

        EnqueueMainThread(() =>
        {
            var farmer = Game1.player;

            if (name != "current")
            {
                var tools = farmer.Items.Where(i => i is Tool).Cast<Tool>().ToList();
                // 精确匹配优先，避免误匹配；Contains 兜底工具等级名（"Pickaxe"→"Iridium Pickaxe"）
                var tool = tools.FirstOrDefault(t => t.Name.Equals(name, StringComparison.OrdinalIgnoreCase)
                        || (t.DisplayName ?? "").Equals(name, StringComparison.OrdinalIgnoreCase))
                    ?? tools.FirstOrDefault(t => t.Name.Contains(name, StringComparison.OrdinalIgnoreCase)
                        || (t.DisplayName ?? "").Contains(name, StringComparison.OrdinalIgnoreCase));

                if (tool == null)
                {
                    tcs.SetResult(new { ok = false, error = $"Tool '{name}' not found in inventory" });
                    return;
                }

                farmer.CurrentToolIndex = farmer.Items.IndexOf(tool);
            }

            if (farmer.CurrentTool is WateringCan wc)
            {
                var facingTile = GetFacingTile(farmer);
                int px = (int)facingTile.X * 64 + 32;
                int py = (int)facingTile.Y * 64 + 32;
                // power >= 0: charged watering, power=-1: single tile (old default)
                int chargePower = power >= 0 ? power : 0;
                wc.DoFunction(farmer.currentLocation, px, py, chargePower, farmer);
                tcs.SetResult(new { ok = true, tool = wc.Name, action = "WateringCan.DoFunction",
                    power = chargePower,
                    tile = new { x = (int)facingTile.X, y = (int)facingTile.Y } });
            }
            else if (farmer.CurrentTool != null)
            {
                if (power >= 0)
                {
                    // Non-watering can with power parameter (e.g. charged hoe)
                    if (farmer.CurrentTool is Hoe hoe)
                    {
                        var facingTile = GetFacingTile(farmer);
                        int px = (int)facingTile.X * 64 + 32;
                        int py = (int)facingTile.Y * 64 + 32;
                        hoe.DoFunction(farmer.currentLocation, px, py, power, farmer);
                        tcs.SetResult(new { ok = true, tool = hoe.Name, action = "Hoe.DoFunction",
                            power, tile = new { x = (int)facingTile.X, y = (int)facingTile.Y } });
                    }
                    else
                    {
                        farmer.BeginUsingTool();
                        farmer.EndUsingTool();
                        tcs.SetResult(new { ok = true, tool = farmer.CurrentTool?.Name ?? "none",
                            note = "power parameter ignored for this tool type" });
                    }
                }
                else
                {
                    if (special && farmer.CurrentTool is MeleeWeapon weapon2)
                    {
                        // 🥊 武器特殊攻击（锤=右键重砸 Super Slam，直接砸不蓄力、有冷却）。
                        // 反编译确认（2026-08-09）：正确入口是 weapon.animateSpecialMove(farmer)——和游戏右键同一路径。
                        //   type 2(锤) → doAnimateSpecialMove 里 triggerClubFunction 重砸 + clubCooldown=6000ms(6秒)。
                        //   ⚠️ triggerClubFunction 不重置 UsingTool/CanMove——程序调用没有农夫 Update 的复位，
                        //      会卡在举着工具的姿势。故重砸后 1.2s 调 farmer.forceCanMove() 强制复位（public 方法）。
                        // 之前直调 DoFunction(power=1) 走普通攻击路径不触发特殊（实测无动画）；勿回退。
                        weapon2.animateSpecialMove(farmer);
                        var f2 = farmer;
                        System.Threading.Tasks.Task.Run(() =>
                        {
                            System.Threading.Thread.Sleep(1200);
                            EnqueueMainThread(() =>
                            {
                                try { f2.forceCanMove(); }
                                catch { }
                            });
                        });
                        tcs.SetResult(new { ok = true, tool = farmer.CurrentTool?.Name ?? "none",
                            action = "weapon_special", wtype = weapon2.type.Value });
                    }
                    else
                    {
                        // 剑/镐子/斧头等：BeginUsingTool+EndUsingTool 触发工具挥击。
                        // ⚠️ 勿改 DoFunction：剑的 DoFunction 不触发攻击动画/判定，会"持剑不挥"。
                        // 炸弹脚本（bomb_common）用这套打怪验证过正常。
                        farmer.BeginUsingTool();
                        farmer.EndUsingTool();
                        tcs.SetResult(new { ok = true, tool = farmer.CurrentTool?.Name ?? "none" });
                    }
                }
            }
        });

        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// GET /weapon_diag — 当前武器内部状态诊断（锤子重砸调试，2026-08-09）。
    /// 报 isOnSpecial / 当前类型冷却(static) / UsingTool / CanMove / 动画帧，
    /// 用于对比"恒手动砸" vs "程序调 /tool special" 的内部差异。
    /// </summary>
    private object HandleWeaponDiag()
    {
        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var farmer = Game1.player;
                var w = farmer.CurrentTool as StardewValley.Tools.MeleeWeapon;
                if (w == null)
                {
                    tcs.SetResult(new { ok = false, error = "current tool is not a MeleeWeapon",
                        currentTool = farmer.CurrentTool?.Name });
                    return;
                }
                int cooldown = w.type.Value switch
                {
                    2 => StardewValley.Tools.MeleeWeapon.clubCooldown,
                    1 => StardewValley.Tools.MeleeWeapon.daggerCooldown,
                    3 => StardewValley.Tools.MeleeWeapon.defenseCooldown,
                    0 => StardewValley.Tools.MeleeWeapon.attackSwordCooldown,
                    _ => 0
                };
                tcs.SetResult(new
                {
                    ok = true,
                    tool = w.Name,
                    wtype = w.type.Value,
                    wspeed = w.speed.Value,
                    isOnSpecial = w.isOnSpecial,
                    cooldown,                     // 当前武器类型冷却 ms：>0 = 刚砸过（手动/程序都会设）
                    farmerUsingTool = farmer.UsingTool,
                    farmerCanMove = farmer.CanMove,
                    animIndex = farmer.FarmerSprite.currentAnimationIndex,
                    animFrame = farmer.FarmerSprite.currentFrame,
                    pauseAnim = farmer.FarmerSprite.PauseForSingleAnimation
                });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// POST /interact  { x?, y? }
    /// 不传 x/y：checkAction 面前格（兼容旧逻辑）。
    /// 传 x/y：直接 checkAction 指定瓦片——支持 8 方向对角交互（SDV 站在(1,1)能右键到(2,2)）。
    /// Returns what's on the tile for context.
    /// </summary>
    private object HandleInteract(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var targetX = GetParamOr(p, "x", -1);
        var targetY = GetParamOr(p, "y", -1);

        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();

        EnqueueMainThread(() =>
        {
            var farmer = Game1.player;
            var loc = farmer.currentLocation;
            var facingTile = GetFacingTile(farmer);
            int ftx = (int)facingTile.X, fty = (int)facingTile.Y;
            var tileVec = new Vector2(ftx, fty);

            // 指定瓦片 → 直接 checkAction 它（8 方向对角交互用），不走面前格
            if (targetX >= 0 && targetY >= 0)
            {
                var tVec = new Vector2(targetX, targetY);
                bool acted = loc.checkAction(new Location(targetX, targetY), Game1.viewport, farmer);
                var res = new Dictionary<string, object?>
                {
                    ["ok"] = true,
                    ["actionTriggered"] = acted,
                    ["facingTile"] = new { x = ftx, y = fty },
                    ["targetTile"] = new { x = targetX, y = targetY }
                };
                if (loc.objects.TryGetValue(tVec, out var tobj))
                    res["object"] = tobj.Name;
                // 家具兜底：checkAction 的家具分支被 didPlayerJustRightClick 卡着（API 驱动没点鼠标），
                // 直接调 checkForAction 触发 TV/日历/壁炉/目录等交互（SDV 1.6）
                if (!acted && TryFurnitureInteract(loc, farmer, targetX, targetY, out var fn))
                {
                    acted = true;
                    res["furniture"] = fn;
                    res["actionTriggered"] = true;
                }
                // 🐟 鱼塘兜底（2026-08-16）：同款坑——checkAction 的鱼塘分支被 didPlayerJustRightClick 卡着，
                // 直接调 FishPond.checkForAction（放鱼/喂食/领产出/开 PondQueryMenu）
                if (!acted && TryFishPondInteract(loc, farmer, targetX, targetY, out var fpName))
                {
                    acted = true;
                    res["fishPond"] = fpName;
                    res["actionTriggered"] = true;
                }
                // 🦀 蟹笼兜底（2026-08-16）：checkAction 的蟹笼挂饵/收产出被 didPlayerJustRightClick 卡着
                if (!acted && TryCrabPotInteract(loc, farmer, targetX, targetY, out var cpName))
                {
                    acted = true;
                    res["crabPot"] = cpName;
                    res["actionTriggered"] = true;
                }
                tcs.SetResult(res);
                return;
            }

            // 先试面前那格
            bool acted2 = loc.checkAction(
                new Location(ftx, fty),
                Game1.viewport,
                farmer
            );

            // 面前没触发，试试玩家站的那格（比如梯子在脚下）
            if (!acted2)
            {
                int ptx = farmer.TilePoint.X, pty = farmer.TilePoint.Y;
                acted2 = loc.checkAction(
                    new Location(ptx, pty),
                    Game1.viewport,
                    farmer
                );
                if (acted2)
                {
                    tcs.SetResult(new Dictionary<string, object?>
                    {
                        ["ok"] = true,
                        ["actionTriggered"] = true,
                        ["facingTile"] = new { x = ftx, y = fty },
                        ["actionAt"] = new { x = ptx, y = pty }
                    });
                    return;
                }
                // 家具兜底（面前格）
                if (TryFurnitureInteract(loc, farmer, ftx, fty, out var fn2))
                {
                    tcs.SetResult(new Dictionary<string, object?>
                    {
                        ["ok"] = true,
                        ["actionTriggered"] = true,
                        ["facingTile"] = new { x = ftx, y = fty },
                        ["furniture"] = fn2,
                        ["actionAt"] = "furniture"
                    });
                    return;
                }
                // 🐟 鱼塘兜底（面前格）
                if (TryFishPondInteract(loc, farmer, ftx, fty, out var fpName2))
                {
                    tcs.SetResult(new Dictionary<string, object?>
                    {
                        ["ok"] = true,
                        ["actionTriggered"] = true,
                        ["facingTile"] = new { x = ftx, y = fty },
                        ["fishPond"] = fpName2,
                        ["actionAt"] = "fishPond"
                    });
                    return;
                }
                // 🦀 蟹笼兜底（面前格）
                if (TryCrabPotInteract(loc, farmer, ftx, fty, out var cpName2))
                {
                    tcs.SetResult(new Dictionary<string, object?>
                    {
                        ["ok"] = true,
                        ["actionTriggered"] = true,
                        ["facingTile"] = new { x = ftx, y = fty },
                        ["crabPot"] = cpName2,
                        ["actionAt"] = "crabPot"
                    });
                    return;
                }
            }

            var result = new Dictionary<string, object?>
            {
                ["ok"] = true,
                ["actionTriggered"] = acted2,
                ["facingTile"] = new { x = ftx, y = fty }
            };

            if (loc.objects.TryGetValue(tileVec, out var obj))
                result["object"] = obj.Name;
            if (loc.terrainFeatures.TryGetValue(tileVec, out var tf))
            {
                result["terrain"] = tf.GetType().Name;
                if (tf is HoeDirt dirt && dirt.crop != null && dirt.readyForHarvest())
                    result["harvestable"] = true;
            }
            var npc = loc.characters.FirstOrDefault(n => n.TilePoint.X == ftx && n.TilePoint.Y == fty);
            if (npc != null)
                result["npc"] = npc.Name;

            tcs.SetResult(result);
        });

        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// POST /furniture_pickup  { "x", "y" }  — 拿起家具（游戏左键 LowPriorityLeftClick 路径）
    /// 传瓦片坐标。要求：玩家站旁边（家具包围盒外扩96px内）、家具可移除（自己摆的，初始家具拿不起）、
    /// 没开菜单、背包有空位（满了静默失败）。家具由游戏下一 tick 自动放回背包（furnitureToRemove 队列）。
    /// 右键 checkAction 在 SDV 1.6 拿不起家具（椅子会坐下/开菜单），必须走这条左键路径。
    /// </summary>
    private object HandleFurniturePickup(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var tx = GetParamOr(p, "x", -1);
        var ty = GetParamOr(p, "y", -1);
        if (tx < 0 || ty < 0)
            return new { ok = false, error = "need x,y tile" };

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var loc = Game1.player.currentLocation;
                int px = tx * 64 + 32, py = ty * 64 + 32;

                // 找出覆盖该像素的家具名（供返回；大家具点任意一格都行）
                string? furnitureName = null;
                foreach (var f in loc.furniture)
                {
                    if (f != null && f.GetBoundingBox().Contains(px, py))
                    {
                        furnitureName = f.Name;
                        break;
                    }
                }

                bool picked = loc.LowPriorityLeftClick(px, py, Game1.player);
                tcs.SetResult(new
                {
                    ok = true,
                    picked,
                    furniture = furnitureName,
                    tile = new { x = tx, y = ty },
                    playerTile = new { x = Game1.player.TilePoint.X, y = Game1.player.TilePoint.Y }
                });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// 家具交互兜底：找覆盖该瓦片的家具并直接调 checkForAction。
    /// 用途：checkAction 的家具分支被 Game1.didPlayerJustRightClick() 卡着（真实鼠标右键才走
    /// checkForAction），AI 用 API 驱动没点鼠标 → 触发不了 TV/日历/壁炉/目录。这里绕过它。
    /// 注意：checkForAction 拿不起普通家具（那走 LowPriorityLeftClick），只负责"交互"。
    /// </summary>
    private bool TryFurnitureInteract(GameLocation loc, Farmer farmer, int tx, int ty, out string? furnitureName)
    {
        furnitureName = null;
        int px = tx * 64 + 32, py = ty * 64 + 32;
        foreach (var f in loc.furniture)
        {
            if (f == null) continue;
            if (f.GetBoundingBox().Contains(px, py))
            {
                if (f.checkForAction(farmer))
                {
                    furnitureName = f.Name;
                    return true;
                }
            }
        }
        return false;
    }

    /// <summary>
    /// 🐟 鱼塘交互兜底：瓦片落在 FishPond 建筑 footprint 里 → 直接调 FishPond.checkForAction。
    /// 用途：checkAction 的鱼塘分支被 Game1.didPlayerJustRightClick() 卡着（真实鼠标右键才走），
    /// AI 用 API 驱动没点鼠标 → 鱼塘交互不触发。绕过它，让游戏原生逻辑处理：
    ///   手持鱼→放进去 / 手持任务物品→喂食 / 空手有产出→领鱼籽 / 否则开 PondQueryMenu。
    /// ⚠️ 2026-08-16 恒确认：鱼塘 checkAction 不触发（只有门瓦片 action=True 但不开菜单），需此绕过。
    /// </summary>
    private bool TryFishPondInteract(GameLocation loc, Farmer farmer, int tx, int ty, out string? pondName)
    {
        pondName = null;
        try
        {
            // 当前地点 + 主农场都扫（鱼塘能放主农场/姜岛农场）
            var candidates = new List<Building>();
            if (loc?.buildings != null) candidates.AddRange(loc.buildings);
            var farm = Game1.getFarm();
            if (farm != null && farm != loc && farm.buildings != null)
                candidates.AddRange(farm.buildings);
            foreach (var b in candidates)
            {
                if (b is not FishPond fp) continue;
                if (b.tileX.Value <= tx && tx < b.tileX.Value + b.tilesWide.Value
                    && b.tileY.Value <= ty && ty < b.tileY.Value + b.tilesHigh.Value)
                {
                    pondName = fp.buildingType.Value ?? "FishPond";
                    // ⚠️ SDV 1.6 FishPond 交互入口是 doAction（不是 checkForAction）
                    fp.doAction(new Vector2(tx, ty), farmer);
                    return true;
                }
            }
        }
        catch (Exception)
        {
            // 交互失败不抛——落回 false
        }
        return false;
    }

    /// <summary>
    /// 🦀 蟹笼交互兜底：瓦片上有 CrabPot → 直接处理挂饵/收产出。
    /// 用途：Object.checkForAction 的蟹笼分支被 didPlayerJustRightClick 卡着（同家具/鱼塘坑，
    /// API 没点真实鼠标右键不触发）。绕过它：
    ///   手持饵(cat -21)+笼无饵 → 挂饵（bait.Value+reduceActiveItemByOne+张口动画）；
    ///   readyForHarvest → 收产出进背包。
    /// </summary>
    private bool TryCrabPotInteract(GameLocation loc, Farmer farmer, int tx, int ty, out string? name)
    {
        name = null;
        try
        {
            if (!loc.objects.TryGetValue(new Vector2(tx, ty), out var obj) || obj is not StardewValley.Objects.CrabPot cp)
                return false;
            name = "Crab Pot";
            // 1) 手持饵 + 笼无饵 → 挂饵
            if (farmer.CurrentItem is StardewValley.Object bait && bait.Category == -21 && cp.bait.Value == null)
            {
                var baitCopy = bait.getOne();
                if (baitCopy is StardewValley.Object baitObj)
                    cp.bait.Value = baitObj;
                farmer.reduceActiveItemByOne();
                cp.lidFlapTimer = 250f;
                return true;
            }
            // 2) 有产出 → 收进背包
            if (cp.readyForHarvest.Value && cp.heldObject.Value is Item outItem)
            {
                if (farmer.addItemToInventoryBool(outItem))
                {
                    cp.heldObject.Value = null;
                    cp.readyForHarvest.Value = false;
                    return true;
                }
            }
            return false;
        }
        catch (Exception)
        {
            return false;
        }
    }

    /// <summary>
    /// GET /furniture  — 扫描当前地点所有家具（名字/位置/尺寸/类型/是否可通行）。
    /// AI 要知道家具摆哪、哪些能交互（TV/日历/壁炉）就靠这个。
    /// </summary>
    private object HandleFurniture(HttpListenerContext ctx)
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var loc = Game1.player.currentLocation;
                var list = loc.furniture.Select(f => new
                {
                    name = SafeDisplayName(f),
                    itemId = f.QualifiedItemId,
                    x = (int)f.TileLocation.X,
                    y = (int)f.TileLocation.Y,
                    width = f.getTilesWide(),
                    height = f.getTilesHigh(),
                    furnitureType = f.furniture_type.Value,
                    isPassable = f.isPassable(),
                    isTV = f is StardewValley.Objects.TV
                }).ToList();
                tcs.SetResult(new { ok = true, location = loc.Name, count = list.Count, furniture = list });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// POST /passable  { x, y }
    /// 判断当前地点的 (x,y) 瓦片是否可走（用 mod 寻路同款 IsTilePassable：
    /// isTilePassable + 家具碰撞 + 物体 isPassable，和 walk_to 实际走的完全一致）。
    /// 供过道格过滤用——剔除墙边/不可站的格，避免 walk_to 绕墙。
    /// </summary>
    private object HandlePassable(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var x = GetParam<int>(p, "x");
        var y = GetParam<int>(p, "y");

        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var loc = Game1.player.currentLocation;
                bool passable = IsTilePassable(loc, new Point(x, y));
                tcs.SetResult(new { ok = true, passable, x, y, location = loc.Name });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// POST /chat  { "message": "Hello!" }
    /// Sends a chat message visible to all players.
    /// </summary>
    private object HandleChat(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var message = GetParam<string>(p, "message");
        var colorName = GetParamOr(p, "color", "white");
        var color = colorName.ToLower() switch
        {
            "hotpink" => Color.HotPink,
            "orange" => Color.Orange,
            "red" => Color.Red,
            "green" => Color.Green,
            _ => Color.White
        };

        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        EnqueueMainThread(() =>
        {
            Game1.chatBox?.addMessage(message, color);
            // ⚠️ 2026-08-15 恒：setText+RecieveCommandInput('\r')（模拟发送广播）只在 farmhand(AI)进程用——
            // 广播出去 author=AI 角色。host(恒)进程只 addMessage 本地显示即可：
            //   否则会顶掉恒正在输入的内容（"反复清空聊天框"根因），且会以恒的名义广播。
            // 恒窗口要看到：addMessage 直接进聊天记录，结算复盘（聊天窗口）也看得见。
            if (Context.IsMultiplayer && Game1.player != null && !Game1.player.IsMainPlayer)
            {
                Game1.chatBox?.setText(message);
                Game1.chatBox?.chatBox.RecieveCommandInput('\r');
            }
        });

        return new { ok = true, message };
    }

    private object HandleChatPush(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var sender = p.TryGetValue("sender", out var s) ? s?.ToString() ?? "Nagi" : "Nagi";
        var message = GetParam<string>(p, "message");
        _chatHud?.AddMessage(sender, message);
        return new { ok = true, sender, message };
    }

    private object HandleChatHistory()
    {
        // Returns empty if chatHud not initialized - safe fallback
        return new { ok = true, messages = Array.Empty<object>() };
    }

    /// <summary>
    /// POST /hud  { "message": "..." }
    /// 屏幕左下角 HUD 通知（Game1.addHUDMessage）——不碰聊天框。
    /// ⚠️ 2026-08-15 恒拍板：**不再用于给 user 推送**（过夜复盘/结算时恒看不到 HUD），
    /// 推送给 user 一律走 /chat（HandleChat 已在 host 进程只 addMessage、不再顶输入）。此端点仅留作诊断。
    /// </summary>
    private object HandleHud(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var message = GetParam<string>(p, "message");
        if (string.IsNullOrEmpty(message))
            return new { ok = false, error = "message required" };

        EnqueueMainThread(() =>
        {
            Game1.addHUDMessage(new HUDMessage(message, 3));
        });
        return new { ok = true, message };
    }

    /// <summary>
    /// GET /mail — 读邮箱：未读队列（Game1.player.mailbox，亮灯的就是这）+ 最近收到的邮件。
    /// 2026-08-15 恒：AI 要能看信——day-start 来信在邮箱队列里，recent_events 推送不到（已读才进 mailReceived）。
    /// </summary>
    private object HandleMail()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");
        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                EnsureMailData();
                var farmer = Game1.player;
                var unread = new List<object>();
                if (farmer.mailbox != null)
                {
                    foreach (var mailId in farmer.mailbox)
                    {
                        if (string.IsNullOrEmpty(mailId)) continue;
                        unread.Add(MailInfo(mailId));
                    }
                }
                var received = new List<object>();
                if (farmer.mailReceived != null)
                {
                    foreach (var mailId in farmer.mailReceived)
                    {
                        if (string.IsNullOrEmpty(mailId) || mailId.StartsWith("_")) continue;
                        received.Add(MailInfo(mailId));
                    }
                }
                tcs.SetResult(new { ok = true, has_mail = unread.Count > 0, unread, received });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    private void EnsureMailData()
    {
        if (_mailDataTried) return;
        _mailDataTried = true;
        try { _mailData = Game1.content.Load<Dictionary<string, string>>("Data/Mail"); }
        catch (Exception ex)
        {
            Monitor.Log($"Data/Mail load fail (Game1.content): {ex.Message}", LogLevel.Warn);
            try { _mailData = Helper.GameContent.Load<Dictionary<string, string>>("Data/Mail"); }
            catch (Exception ex2) { Monitor.Log($"Data/Mail load fail (Helper.GameContent): {ex2.Message}", LogLevel.Warn); }
        }
    }

    private object MailInfo(string mailId)
    {
        string title = mailId, body = mailId;
        if (_mailData != null && _mailData.TryGetValue(mailId, out var raw))
        {
            var parts = raw.Split('/');
            // SDV Data/Mail: 0=text 1=sender 2=date 3=time 4=repeatDay 5=title
            if (parts.Length > 0) body = parts[0].Trim();
            foreach (int idx in new[] { 5, 1 })
            {
                if (parts.Length > idx)
                {
                    var cand = parts[idx].Trim();
                    if (cand.Length > 0 && !cand.All(char.IsDigit)) { title = cand; break; }
                }
            }
        }
        return new { id = mailId, title, body };
    }

    /// <summary>
    /// POST /emote  { "emote": "heart" } 或 { "id": 32 }
    /// 发表情。2026-08-11：
    /// - `emote` 字符串走 netDoEmote（网络同步，恒能看到）——正确姿势
    /// - `id` 整数走旧版 doEmote(int)（本地精灵图，联机看不到）——仅兼容
    /// 常用 emote 字符串: happy/sad/heart/exclamation/note/sleep/game/question/x/pause/blush/angry/yes/no
    /// </summary>
    private object HandleEmote(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var emote = GetParamOr(p, "emote", "");
        var id = GetParamOr(p, "id", -1);

        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        EnqueueMainThread(() =>
        {
            if (!string.IsNullOrEmpty(emote))
                Game1.player.netDoEmote(emote);
            else if (id >= 0)
                Game1.player.doEmote(id);
        });

        return new { ok = true, emote, emoteId = id };
    }

    /// <summary>
    /// GET /state  ?consume_events=true
    /// Returns comprehensive game state. consume_events=true 时返回 recent_events 后清空（"看过即清空"）。
    /// </summary>
    /// <summary>📮 当前玩家邮箱坐标（小屋/农舍邮箱；邮箱不是 loc.objects，/surroundings 扫不到）。
    /// 返回 {x, y, location}，AI 据此 map go Farm + 走过去 + /interact 正常读信领附件。</summary>
    private object? TryGetMailbox()
    {
        try
        {
            var p = Game1.player.getMailboxPosition();
            return new { x = p.X, y = p.Y, location = Game1.getFarm()?.Name ?? "Farm" };
        }
        catch { return null; }
    }

    /// <summary>
    /// 🧪 当前生效 buff 列表（/state 用）：name + 剩余/总时长。buff 生效/结束提醒（2026-08-16 恒）。
    /// </summary>
    private List<object>? EnumerateBuffs(Farmer farmer)
    {
        try
        {
            var list = new List<object>();
            var buffsProp = farmer.GetType().GetProperty("buffs");
            var buffsObj = buffsProp?.GetValue(farmer);
            if (buffsObj == null) return list;
            IEnumerable<Buff>? buffs = buffsObj as IEnumerable<Buff>;
            if (buffs == null)
            {
                var act = buffsObj.GetType().GetField("activeBuffs",
                    System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
                buffs = act?.GetValue(buffsObj) as IEnumerable<Buff>;
            }
            if (buffs == null) return list;
            foreach (var b in buffs)
            {
                if (b == null || !b.visible) continue;
                list.Add(new
                {
                    name = b.displayName ?? b.id ?? "",
                    remainingMs = b.millisecondsDuration,
                    totalMs = b.totalMillisecondsDuration,
                });
            }
            return list;
        }
        catch (Exception)
        {
            return null;
        }
    }

    private object HandleState(HttpListenerContext ctx)
    {
        if (!Context.IsWorldReady)
            return new { ok = true, worldReady = false };

        // 看过即清空：AI 渲染小新闻的这次读取把事件消费掉，天然"截至上一次调用"
        bool consume = (ctx.Request.QueryString["consume_events"] ?? "") == "true";
        // light=true：不返回背包物品明细（只有 name/stack/slotIndex），状态条只需格数，省解析。
        // 想看明细调 check_backpack（不带 light）。
        bool light = (ctx.Request.QueryString["light"] ?? "") == "true";
        var eventsSnapshot = GetRecentEventsSnapshot();
        if (consume)
        {
            lock (_recentEvents)
            {
                _recentEvents.Clear();
            }
        }

        var farmer = Game1.player;
        var loc = farmer.currentLocation;

        var npcs = loc.characters
            .Select(n => new
            {
                name = n.Name,
                x = n.TilePoint.X,
                y = n.TilePoint.Y
            }).ToList();

        // ⚠️ 2026-08-10 修复：.Where(i=>i!=null) 是压缩列表，index≠真实槽位（有空槽时点错格子）。
        //    改用带原始 index 的 Select，报 slotIndex 真实槽位，AI 点背包格/放锻造槽用 slotIndex 定位。
        // light=true 只报 name/stack/slotIndex（状态条只需格数），跳过 SafeSellPrice/DescribeItemStats 重活
        List<object> inventory;
        if (light)
        {
            inventory = farmer.Items.Select((i, slotIdx) => new { i, slotIdx })
                .Where(x => x.i != null)
                .Select(x => (object)new Dictionary<string, object?>
                {
                    ["name"] = x.i.Name,
                    ["stack"] = x.i.Stack,
                    ["slotIndex"] = x.slotIdx
                }).ToList();
        }
        else
        {
            inventory = farmer.Items.Select((i, slotIdx) => new { i, slotIdx })
                .Where(x => x.i != null)
                .Select(x =>
                {
                    var i = x.i;
                    var entry = new Dictionary<string, object?>
                    {
                        ["name"] = i.Name,
                        ["displayName"] = i.DisplayName,
                        ["stack"] = i.Stack,
                        ["category"] = i.getCategoryName(),
                        ["catNum"] = i.Category,
                        ["quality"] = (i as StardewValley.Object)?.Quality ?? 0,
                        ["value"] = SafeSellPrice(i),
                        ["stats"] = DescribeItemStats(i),
                        ["slotIndex"] = x.slotIdx   // 真实背包槽位（点坐标用这个，不是列表 index）
                    };
                    if (i is WateringCan wc)
                    {
                        entry["waterLeft"] = wc.WaterLeft;
                        entry["waterMax"] = wc.waterCanMax;
                    }
                    if (i is MeleeWeapon mw)
                    {
                        // 武器原生类型：0剑 1锤 2匕首 3镰（用类型判断比靠名字猜可靠——镐子坑的反面教材）
                        entry["wtype"] = mw.type.Value;
                        // 武器速度 stat（含附魔附的速度）：挥击间隔自适应用
                        entry["wspeed"] = mw.speed.Value;
                    }
                    return (object)entry;
                }).ToList();
        }

        var menuInfo = (object?)null;
        if (Game1.activeClickableMenu != null)
        {
            var menuType = Game1.activeClickableMenu.GetType().Name;
            var dialogueText = "";
            if (Game1.activeClickableMenu is StardewValley.Menus.DialogueBox db)
            {
                try { dialogueText = db.getCurrentString() ?? ""; } catch { }
            }
            // 对话框当前说话人（NPC 名，供"在和xx搭话"）
            var dialogueSpeaker = "";
            if (Game1.activeClickableMenu is StardewValley.Menus.DialogueBox dbSpeaker)
            {
                try
                {
                    var chField = typeof(DialogueBox).GetField("character",
                        System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic |
                        System.Reflection.BindingFlags.Instance);
                    var ch = chField?.GetValue(dbSpeaker) as NPC;
                    if (ch == null)
                    {
                        var chProp = typeof(DialogueBox).GetProperty("character",
                            System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic |
                            System.Reflection.BindingFlags.Instance);
                        ch = chProp?.GetValue(dbSpeaker) as NPC;
                    }
                    if (ch != null) dialogueSpeaker = ch.Name;
                }
                catch { }
            }
            // GameMenu 当前子页（背包/合成/社交…），供"视窗口内容而定"
            var submenuType = "";
            if (Game1.activeClickableMenu is StardewValley.Menus.GameMenu gm)
            {
                try
                {
                    var pagesField = typeof(GameMenu).GetField("pages",
                        System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic |
                        System.Reflection.BindingFlags.Instance);
                    var tabField = typeof(GameMenu).GetField("currentTab",
                        System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic |
                        System.Reflection.BindingFlags.Instance);
                    var tab = tabField?.GetValue(gm);
                    if (pagesField?.GetValue(gm) is IClickableMenu[] pagesArr
                        && tab is int tabIdx && tabIdx >= 0 && tabIdx < pagesArr.Length && pagesArr[tabIdx] != null)
                    {
                        submenuType = pagesArr[tabIdx].GetType().Name;
                    }
                    else if (pagesField?.GetValue(gm) is List<IClickableMenu> pagesList
                        && tab is int tabIdx2 && tabIdx2 >= 0 && tabIdx2 < pagesList.Count && pagesList[tabIdx2] != null)
                    {
                        submenuType = pagesList[tabIdx2].GetType().Name;
                    }
                }
                catch { }
            }
            // 尝试读取对话框选项（Clint 的商店/升级/砸晶球/离开等）
            string[]? responses = null;
            if (Game1.activeClickableMenu is DialogueBox dbWithResp)
            {
                try
                {
                    var respField = typeof(DialogueBox).GetField("responses",
                        System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic |
                        System.Reflection.BindingFlags.Instance);
                    // ⚠️ SDV 1.6 responses 是 Response[]，用 IEnumerable 兼容
                    var respArr = respField?.GetValue(dbWithResp) as IEnumerable<Response>;
                    var respList = respArr?.ToList();
                    if (respList != null && respList.Count > 0)
                        responses = respList.Select(r => r.responseText).ToArray();
                }
                catch { }
            }

            // ⚠️ 光标手持物品（锻造合成/商店买后 heldItem）——2026-08-10 恒反馈：
            //   AI 不知道合成完光标拿着戒指，乱点又放回锻造台。SDV 各版本字段/属性名不一
            //   （IClickableMenu.heldItem / Farmer.CursorSlot / NetRef<Item> / 组件.item），
            //   **穷举字段+属性+基类+Player** 全面抓，按 Item 值判断。
            object? heldItem = null;
            var heldFlags = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic
                | System.Reflection.BindingFlags.Instance;

            void TryCaptureItem(object? obj)
            {
                if (obj == null || heldItem != null) return;
                if (obj is Item oi && oi != null) { heldItem = new { name = oi.Name, stack = oi.Stack }; return; }
                if (obj is Netcode.NetRef<Item> oni && oni.Value != null)
                { heldItem = new { name = oni.Value.Name, stack = oni.Value.Stack }; return; }
            }
            try
            {
                var heldMenuType = Game1.activeClickableMenu.GetType();
                // 1) 所有字段（含基类）
                for (var t = heldMenuType; t != null && heldItem == null; t = t.BaseType)
                    foreach (var f in t.GetFields(heldFlags))
                        TryCaptureItem(f.GetValue(Game1.activeClickableMenu));
                // 2) 所有可读属性（含基类）
                for (var t = heldMenuType; t != null && heldItem == null; t = t.BaseType)
                    foreach (var p in t.GetProperties(heldFlags))
                        if (p.CanRead && p.GetIndexParameters().Length == 0)
                            TryCaptureItem(p.GetValue(Game1.activeClickableMenu));
                // 3) Farmer 上的 Item 字段（CursorSlot 等，字段名/类型因版本而异）
                if (heldItem == null)
                    foreach (var f in typeof(Farmer).GetFields(heldFlags))
                        TryCaptureItem(f.GetValue(Game1.player));
            }
            catch { }

            menuInfo = new
            {
                type = menuType,
                dialogue = string.IsNullOrEmpty(dialogueText) ? null : dialogueText,
                responses = responses,
                speaker = string.IsNullOrEmpty(dialogueSpeaker) ? null : dialogueSpeaker,
                submenu = string.IsNullOrEmpty(submenuType) ? null : submenuType,
                heldItem = heldItem,
                // 🎁 送礼菜单（冬星节神秘礼物等：ItemGrabMenu+reverseGrab/behaviorFunction）：
                // 点物品=送出（receiveLeftClick 内部调 behaviorFunction），不是拿起。状态条据此注入提示。
                gift = Game1.activeClickableMenu is StardewValley.Menus.ItemGrabMenu giftIgm
                       && (giftIgm.reverseGrab || giftIgm.behaviorFunction != null)
            };
        }

        var eventInfo = (object?)null;
        if (loc.currentEvent != null)
        {
            var ev = loc.currentEvent;
            string? evDialogue = null;
            if (Game1.activeClickableMenu is DialogueBox evDb)
            {
                try { evDialogue = evDb.getCurrentString(); } catch { }
            }
            eventInfo = new
            {
                id = ev.id,
                skippable = ev.skippable,
                message = evDialogue
            };
        }

        return new
        {
            ok = true,
            worldReady = true,
            player = new
            {
                name = farmer.Name,
                x = farmer.TilePoint.X,
                y = farmer.TilePoint.Y,
                health = farmer.health,
                maxHealth = farmer.maxHealth,
                stamina = farmer.Stamina,
                maxStamina = farmer.MaxStamina,
                money = farmer.Money,
                clubCoins = farmer.clubCoins,   // 🪙 赌场齐币余额（2026-08-23 恒：赌场商店/读齐币用；SDV 用 clubCoins 存）
                minigame = Game1.currentMinigame?.GetType().Name,   // 🎰 当前小游戏(Slots/CalicoJack)；null=无（2026-08-23 恒：牌局/老虎机读端点用）
                currentTool = farmer.CurrentTool?.Name,
                currentItem = farmer.CurrentItem?.Name,   // 📚 手持物品名（书是Object非Tool，CurrentTool会null；2026-08-16恒测读书）
                currentToolUpgrade = (farmer.CurrentTool as Tool)?.UpgradeLevel ?? -1,
                facingDirection = farmer.FacingDirection,
                mineHardMode = Game1.player.team?.mineShrineActivated?.Value ?? false,  // 困难矿井（神庙激活），整理频率用
                dailyLuck = Game1.player.DailyLuck,
                maxItems = farmer.MaxItems,
                isMoving = _pathQueue != null && _pathQueue.Count > 0,
                stationarySeconds = (int)_stationarySeconds,
                isInBed = farmer.isInBed.Value,   // 🧾 等睡/纯聊天环节检测用（2026-08-17 恒）
                festivalScore = farmer.festivalScore,   // 🥚 蛋蛋节捡蛋进度（festival eggrun 用，2026-08-17）
                buffs = EnumerateBuffs(farmer),
                fishing = farmer.CurrentTool is FishingRod rod ? new
                {
                    isCasting = rod.isTimingCast,
                    isFishing = rod.isFishing,
                    isNibbling = rod.isNibbling,
                    isReeling = rod.isReeling,
                    hit = rod.hit
                } : null,
                riding = farmer.mount != null ? new
                {
                    name = farmer.mount.Name,
                    x = farmer.mount.TilePoint.X,
                    y = farmer.mount.TilePoint.Y
                } : null,
                homeLocation = farmer.homeLocation.Value,
                homeDoor = FindHomeDoor(farmer),
                // 🆕 2026-08-18 正在升级/待取的工具名（toolBeingUpgraded 在玩家去铁匠铺领取前一直非空；
                //     AI 任何时候查 /state 都能发现"有工具在铁匠铺待取"）
                toolUpgrading = farmer.toolBeingUpgraded?.Value?.DisplayName
            },
            location = new
            {
                name = loc.Name,
                mapWidth = loc.Map.DisplayWidth / 64,
                mapHeight = loc.Map.DisplayHeight / 64
            },
            // 📮 邮箱位置（2026-08-15：邮箱不是 loc.objects 实体，是地图"MailboxLocation"画的特殊点，
            //     /surroundings 扫不到；AI 用这坐标 map go Farm + 走过去再 /interact 读信领附件）
            mailbox = TryGetMailbox(),
            time = new
            {
                timeOfDay = Game1.timeOfDay,
                dayOfMonth = Game1.dayOfMonth,
                season = Game1.currentSeason,
                year = Game1.year,
                weather = Game1.isGreenRain ? 7 : Game1.isRaining ? 1 : Game1.isLightning ? 2 : Game1.isSnowing ? 5 : Game1.isDebrisWeather ? 3 : 0,
                isRaining = Game1.isRaining,
                isSnowing = Game1.isSnowing,
                isLightning = Game1.isLightning,
                isGreenRain = Game1.isGreenRain,
            },
            farmType = Game1.whichFarm,
            farmTypeName = Game1.whichFarm switch
            {
                // ⚠️ 2026-08-15 修：之前整体错位一位（3 标成 Forest）——SDV 1.6: 0标准 1河畔 2森林 3山地 4荒野 5四角 6沙滩 7草地
                0 => "Standard(标准)", 1 => "Riverland(河畔)", 2 => "Forest(森林)", 3 => "Hilltop(山地)",
                4 => "Wilderness(荒野)", 5 => "FourCorners(四角)", 6 => "Beach(沙滩)", 7 => "Meadowlands(草地)",
                _ => $"Unknown({Game1.whichFarm})"
            },
            activeMenu = menuInfo,
            activeEvent = eventInfo,
            in_dialogue = Game1.activeClickableMenu is StardewValley.Menus.DialogueBox,
            recent_events = eventsSnapshot,
            npcs,
            inventory,
            otherPlayers = Game1.otherFarmers.Values
                .Where(f => f.currentLocation != null)
                .Select(f => new
                {
                    name = f.Name,
                    location = f.currentLocation.Name,
                    x = f.TilePoint.X,
                    y = f.TilePoint.Y
                }).ToList()
        };
    }

    private List<Dictionary<string, object?>>? GetRecentEventsSnapshot()
    {
        lock (_recentEvents)
        {
            return _recentEvents.Count > 0
                ? new List<Dictionary<string, object?>>(_recentEvents)
                : null;
        }
    }

    /// <summary>
    /// GET /surroundings  ?radius=10
    /// Returns tile info around the player: passability, objects, terrain features, buildings, NPCs.
    /// </summary>
    /// <summary>
    /// GET /warps
    /// Returns all locations' warp/exit tiles (full cross-map warp graph).
    /// 用于动态校准 locations.py 的 POI 出入口坐标，不用一张张地图走过去标。
    /// </summary>
    private object HandleWarps()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        EnsureWarpGraph();
        var result = new Dictionary<string, List<object>>();
        foreach (var kv in _warpGraph!)
        {
            result[kv.Key] = kv.Value.Select(l => (object)new
            {
                x = l.SourceX,
                y = l.SourceY,
                targetLocation = l.TargetLocation,
                targetX = l.DestX,
                targetY = l.DestY
            }).ToList();
        }
        return new { ok = true, maps = result };
    }

    /// <summary>
    /// GET /farm_buildings
    /// Returns all farm buildings (type, tile, door). 农场建筑会变动，动态扫。
    /// </summary>
    private object HandleFarmBuildings()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var farm = Game1.getLocationFromName("Farm");
        var buildings = new List<Dictionary<string, object?>>();
        if (farm != null)
        {
            foreach (var b in farm.buildings)
            {
                var entry = new Dictionary<string, object?>
                {
                    ["type"] = b.buildingType.Value ?? b.GetType().Name,
                    ["x"] = b.tileX.Value,
                    ["y"] = b.tileY.Value,
                    ["width"] = b.tilesWide.Value,
                    ["height"] = b.tilesHigh.Value
                };
                if (b.humanDoor.Value != Point.Zero)
                {
                    entry["doorX"] = b.tileX.Value + b.humanDoor.X;
                    entry["doorY"] = b.tileY.Value + b.humanDoor.Y;
                }
                if (b.indoors?.Value is GameLocation il)
                    entry["indoorsName"] = il.Name;  // 室内真实 location 名（此前误读成翻译串 "Building: indoors"）
                buildings.Add(entry);
            }
        }
        return new { ok = true, count = buildings.Count, buildings };
    }

    /// <summary>
    /// POST /fish_pond  { x?, y?, action? }
    /// 🐟 鱼塘状态读取（只读）。action: list(全部) / status(指定 x,y)。
    /// 放鱼/喂食/领产出走 /interact 鱼塘 bypass（手持对应物品 + 交互），游戏原生 checkForAction 处理。
    /// ⚠️ 2026-08-16：鱼塘 checkAction 被 didPlayerJustRightClick 卡着，见 TryFishPondInteract。
    /// </summary>
    private object HandleFishPond(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var x = GetParamOr(p, "x", -1);
        var y = GetParamOr(p, "y", -1);
        var action = GetParamOr<string>(p, "action", "list");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var farm = Game1.getFarm();
                if (farm == null) { tcs.SetResult(new { ok = false, error = "Farm not loaded" }); return; }

                var ponds = farm.buildings.OfType<FishPond>().ToList();
                if (ponds.Count == 0)
                {
                    tcs.SetResult(new { ok = true, count = 0, ponds = Array.Empty<object>() });
                    return;
                }

                FishPond? target = null;
                if (x >= 0 && y >= 0)
                    target = ponds.FirstOrDefault(fp =>
                        fp.tileX.Value <= x && x < fp.tileX.Value + fp.tilesWide.Value
                        && fp.tileY.Value <= y && y < fp.tileY.Value + fp.tilesHigh.Value);

                object StateOf(FishPond fp)
                {
                    // ⚠️ SDV 1.6 FishPond：fishType=NetString(合格ID)、FishCount=属性、容量=maxOccupants、
                    //    任务=neededItem(NetRef<Item>)+neededItemCount、完成=hasCompletedRequest、
                    //    产出=output(NetRef<Item>)、天数=daysSinceSpawn。
                    string fish = "空";
                    var fishId = fp.fishType.Value ?? "";
                    if (!string.IsNullOrEmpty(fishId) && fishId != "-1")
                    {
                        try { fish = ItemRegistry.GetData(fishId)?.DisplayName ?? fishId; }
                        catch { fish = fishId; }
                    }
                    string? quest = null;
                    if (fp.neededItem.Value is Item qitem && !fp.hasCompletedRequest.Value)
                        quest = qitem.DisplayName;
                    int questCount = fp.neededItemCount?.Value ?? 0;
                    return new
                    {
                        x = fp.tileX.Value, y = fp.tileY.Value,
                        fish = fish, fishId = fishId,
                        count = fp.FishCount, capacity = fp.maxOccupants.Value,
                        questItem = quest, questCount = questCount,
                        questDone = fp.hasCompletedRequest.Value,
                        output = fp.output?.Value?.DisplayName,
                        outputId = fp.output?.Value?.QualifiedItemId,
                        daysSinceSpawn = fp.daysSinceSpawn?.Value,
                        completedRequest = fp.hasCompletedRequest.Value,
                        golden = fp.goldenAnimalCracker?.Value == true,   // 🥇 金框=喂了动物饼干（双倍产出）
                    };
                }

                // 🎣 服务端直钓：不碰竿/鼠标（右键拉竿有误触"吃鱼"风险），直接 CatchFish 把鱼给玩家。
                if (action == "fish" || action == "钓")
                {
                    if (target == null)
                    {
                        if (x >= 0 || y >= 0) { tcs.SetResult(new { ok = false, error = "该坐标没有鱼塘" }); return; }
                        target = ponds.FirstOrDefault();
                    }
                    if (target == null || target.FishCount <= 0)
                    {
                        tcs.SetResult(new { ok = false, error = "鱼塘没有鱼可钓" });
                        return;
                    }
                    var caught = target.CatchFish();
                    if (caught is Item fishItem)
                    {
                        if (!Game1.player.addItemToInventoryBool(fishItem))
                        {
                            tcs.SetResult(new { ok = false, error = "背包满了，放不下鱼", fish = fishItem.DisplayName });
                            return;
                        }
                        // ⚠️ SDV1.6 计数走 currentOccupants（FishCount 属性读它）；拿一条减一
                        target.currentOccupants.Value = Math.Max(0, target.currentOccupants.Value - 1);
                        tcs.SetResult(new { ok = true, fish = fishItem.DisplayName,
                            count = target.FishCount, pond = StateOf(target) });
                        return;
                    }
                    tcs.SetResult(new { ok = false, error = "CatchFish 返回类型异常" });
                    return;
                }

                if (action == "status" || target != null)
                {
                    if (target == null) { tcs.SetResult(new { ok = false, error = "该坐标没有鱼塘" }); return; }
                    tcs.SetResult(new { ok = true, pond = StateOf(target) });
                    return;
                }
                tcs.SetResult(new { ok = true, count = ponds.Count, ponds = ponds.Select(fp => StateOf(fp)).ToList() });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// GET /find_npc?name=X
    /// 扫全地图找 NPC 当前位置（社交用：找人→走过去→搭话/送礼）。
    /// 按 英文名/中文displayName 子串匹配，返回所有匹配项。
    /// </summary>
    private object HandleFindNpc(HttpListenerContext ctx)
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var name = (ctx.Request.QueryString["name"] ?? "").Trim();
        var results = new List<Dictionary<string, object?>>();
        if (name.Length > 0)
        {
            foreach (var loc in Game1.locations)
            {
                if (loc?.characters == null) continue;
                foreach (var n in loc.characters)
                {
                    var en = n.Name ?? "";
                    var zh = n.displayName ?? "";
                    bool hit = en.Equals(name, StringComparison.OrdinalIgnoreCase)
                        || zh.Equals(name)
                        || en.Contains(name, StringComparison.OrdinalIgnoreCase)
                        || zh.Contains(name);
                    if (!hit) continue;
                    results.Add(new Dictionary<string, object?>
                    {
                        ["name"] = en,
                        ["displayName"] = zh,
                        ["location"] = loc.Name,
                        ["x"] = n.TilePoint.X,
                        ["y"] = n.TilePoint.Y,
                        ["facing"] = n.FacingDirection,
                        ["isSleeping"] = n.isSleeping.Value,
                        ["isVillager"] = n is NPC && !(n is StardewValley.Monsters.Monster),
                        ["dialogue"] = n.CurrentDialogue?.Count > 0
                            ? n.CurrentDialogue.Peek()?.getCurrentDialogue()?.Trim()
                            : null
                    });
                }
            }
        }
        return new { ok = true, name, count = results.Count, npcs = results };
    }

    /// <summary>
    /// GET /trinkets
    /// 查询已装备的饰品及其属性（如魔法箭筒的发射间隔）。
    /// </summary>
    private object HandleTrinkets()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var farmer = Game1.player;
        var result = new List<Dictionary<string, object?>>();
        try
        {
            foreach (var item in farmer.trinketItems)
            {
                if (item == null) continue;
                var entry = new Dictionary<string, object?>
                {
                    ["name"] = item.Name,
                    ["quality"] = item.Quality
                };
                // 读饰品效果属性（发射间隔等）
                try
                {
                    var effect = item.GetType().GetProperty("TrinketEffect",
                        System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance)?.GetValue(item);
                    if (effect != null)
                    {
                        var props = new Dictionary<string, object?>();
                        foreach (var f in effect.GetType().GetFields(
                            System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance))
                        {
                            try { props[f.Name] = f.GetValue(effect); } catch { }
                        }
                        entry["effect"] = props;
                    }
                }
                catch { }
                result.Add(entry);
            }
        }
        catch { }
        return new { ok = true, trinkets = result };
    }

    /// <summary>
    /// POST /trinket { "name": "MagicQuiver" }
    /// 装备饰品：从背包找同名饰品，装进饰品槽。
    /// </summary>
    private object HandleTrinketEquip(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var name = GetParam<string>(p, "name");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var farmer = Game1.player;
                for (int i = 0; i < farmer.Items.Count; i++)
                {
                    var item = farmer.Items[i];
                    if (item is StardewValley.Objects.Trinkets.Trinket tr
                        && tr.Name.Equals(name, StringComparison.OrdinalIgnoreCase))
                    {
                        // 先脱旧饰品回背包（避免直接覆盖顶掉丢失），再穿新的
                        var oldTrinket = farmer.trinketItems.FirstOrDefault();
                        if (oldTrinket != null)
                        {
                            farmer.addItemToInventory(oldTrinket);
                            farmer.trinketItems.Clear();
                        }
                        farmer.trinketItems.Add(tr);
                        farmer.Items[i] = null;  // 移出背包
                        tcs.SetResult(new { ok = true, equipped = tr.Name });
                        return;
                    }
                }
                tcs.SetResult(new { ok = false, error = $"饰品 '{name}' 不在背包" });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// GET /rings — 查询左右手戒指。
    /// </summary>
    private object HandleRings()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");
        var farmer = Game1.player;
        return new { ok = true, left = farmer.leftRing?.Name, right = farmer.rightRing?.Name };
    }

    /// <summary>
    /// POST /ring { "name", "slot"?: "left"/"right" }
    /// 装备戒指：从背包找同名戒指装到对应槽（默认右手）。
    /// </summary>
    private object HandleRingEquip(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var name = GetParam<string>(p, "name");
        var slot = GetParamOr(p, "slot", "right");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var farmer = Game1.player;
                for (int i = 0; i < farmer.Items.Count; i++)
                {
                    if (farmer.Items[i] is StardewValley.Objects.Ring ring
                        && ring.Name.Equals(name, StringComparison.OrdinalIgnoreCase))
                    {
                        // leftRing/rightRing 是 readonly NetRef<Ring>——SetValue 整个对象会被 readonly 忽略，
                        // 要 GetValue 拿现成 NetRef 再设 .Value（NetField 正确赋值，能触发网络同步）
                        var field = typeof(Farmer).GetField(slot.Equals("left", StringComparison.OrdinalIgnoreCase) ? "leftRing" : "rightRing",
                            System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
                        var netRef = field?.GetValue(farmer) as Netcode.NetRef<StardewValley.Objects.Ring>;
                        if (netRef == null)
                        {
                            tcs.SetResult(new { ok = false, error = "找不到戒指槽" });
                            return;
                        }
                        // 先脱旧戒指回背包（避免直接覆盖顶掉丢失），再穿新的
                        var old = netRef.Value;
                        if (old != null)
                            farmer.addItemToInventory(old);
                        netRef.Value = ring;
                        farmer.Items[i] = null;  // 新戒指从背包移除
                        tcs.SetResult(new { ok = true, equipped = ring.Name, slot });
                        return;
                    }
                }
                tcs.SetResult(new { ok = false, error = $"戒指 '{name}' 不在背包" });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// POST /equip { "name": "铁头靴" } 或 { "slot": "boots" }
    /// 通用穿/脱工具：按物品类型自动判定槽位（衣服/裤子/帽子/鞋/左右戒指/饰品），
    /// 替下旧物回背包、新物戴上。AI 不用知道槽位，脚本自动定位——一个工具搞定所有穿戴。
    /// - name 模式（穿戴）：背包找同名物品 → 定位槽位 → 旧物回背包 → 新物戴上。
    /// - slot 模式（脱下）：把某槽卸回背包（需背包有空格）。
    /// 戒指自动：左手空则左手、否则右手（替右手）。返回 slot + replaced 让 AI 确认。
    /// </summary>
    private object HandleEquip(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        // name 用 GetParamOr 允许为空（slot 模式不传 name）
        var name = GetParamOr<string>(p, "name", "");
        var slotParam = GetParamOr<string>(p, "slot", "").ToLowerInvariant();
        // 戒指指定换哪只手：1/left（左手）或 2/right（右手）；也可传"要替换掉的那枚戒指名"，脚本自动找它在哪只手。
        var hand = GetParamOr<string>(p, "hand", "").ToLowerInvariant();

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var farmer = Game1.player;

                // ── slot 模式：脱下（把某槽卸回背包）──
                if (string.IsNullOrWhiteSpace(name))
                {
                    if (string.IsNullOrWhiteSpace(slotParam))
                    {
                        tcs.SetResult(new { ok = false, error = "需要 name（穿某物）或 slot（脱某槽，如 boots）" });
                        return;
                    }
                    var off = TryTakeOff(farmer, slotParam, out var offName, out var offErr);
                    tcs.SetResult(off
                        ? new { ok = true, action = "off", slot = slotParam, removed = offName }
                        : new { ok = false, error = offErr ?? $"无法脱下 {slotParam}" });
                    return;
                }

                // ── name 模式：穿戴（按物品类型定位槽位 + 替换）──
                for (int i = 0; i < farmer.Items.Count; i++)
                {
                    var item = farmer.Items[i];
                    if (item == null) continue;
                    if (!item.Name.Equals(name, StringComparison.OrdinalIgnoreCase)) continue;

                    var err = TryEquip(farmer, item, i, hand, out var slot, out var replaced);
                    tcs.SetResult(err == null
                        ? new { ok = true, action = "on", slot, equipped = item.Name, replaced }
                        : new { ok = false, error = err });
                    return;
                }
                tcs.SetResult(new { ok = false, error = $"背包没有 '{name}'" });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>把背包第 idx 格物品穿上：按物品类型自动定位槽位，用游戏原生 Farmer.Equip（真穿脱，
    /// 自动 onUnequip/onEquip、触发 buff、联网同步），旧物回背包、不销毁。返回 null=成功，否则错误串。
    /// 衣服/裤子额外把覆盖值(shirt/pants NetString)设成"-1"让渲染走真件（保染色）；帽/鞋/戒指无覆盖值概念。</summary>
    private string? TryEquip(Farmer farmer, Item item, int idx, string hand, out string slot, out string? replaced)
    {
        slot = ""; replaced = null;

        // ① 鞋
        if (item is StardewValley.Objects.Boots b)
        {
            slot = "boots";
            var old = farmer.Equip(b, farmer.boots);   // 真穿：旧物返回、onEquip 生效防御
            if (old != null) { if (!farmer.addItemToInventoryBool(old)) return "背包满，装不下旧鞋"; replaced = old.Name; }
            farmer.Items[idx] = null;
            return null;
        }

        // ② 戒指：hand 指定换哪只手（1/left、2/right，或某枚已戴戒指的 name 找到它在哪只手）；未指定→左空则左，否则右。
        if (item is StardewValley.Objects.Ring r)
        {
            var resolved = ResolveRingHand(farmer, hand);   // null=未指定
            bool useLeft = resolved == "left"
                || (resolved == null && farmer.leftRing.Value == null);
            slot = useLeft ? "leftRing" : "rightRing";
            var slotRef = useLeft ? farmer.leftRing : farmer.rightRing;
            var old = farmer.Equip(r, slotRef);
            if (old != null) { if (!farmer.addItemToInventoryBool(old)) return "背包满，装不下旧戒指"; replaced = old.Name; }
            farmer.Items[idx] = null;
            return null;
        }

        // ③ 饰品（NetList<Trinket>：Add/Clear 触发 OnTrinketArrayReplaced 自动应用/卸载效果）
        // ⚠️ 2026-08-23 恒：战斗精通==解锁饰品槽；未领 → 禁止穿（同铁砧"没有力量用"）。
        // 判据=已学配方含 Anvil（铁砧=战斗精通奖励配方；/craft_recipes 只报已学，同 Python _mastery_claimed）。
        if (IsTrinket(item))
        {
            try
            {
                if (farmer.craftingRecipes?.ContainsKey("Anvil") != true)
                    return "未解锁战斗精通（铁砧/饰品槽），无法装备饰品";
            }
            catch { /* 读不到已学配方 → 常规放行，不误伤 */ }
            slot = "trinket";
            var old = farmer.trinketItems.FirstOrDefault();
            if (old != null)
            {
                if (!farmer.addItemToInventoryBool(old)) return "背包满，装不下旧饰品";
                replaced = old.Name;
                farmer.trinketItems.Clear();
            }
            farmer.trinketItems.Add((StardewValley.Objects.Trinkets.Trinket)item);
            farmer.Items[idx] = null;
            return null;
        }

        // ④ 帽
        if (item is StardewValley.Objects.Hat h)
        {
            slot = "hat";
            var old = farmer.Equip(h, farmer.hat);
            if (old != null) { if (!farmer.addItemToInventoryBool(old)) return "背包满，装不下旧帽"; replaced = old.Name; }
            farmer.Items[idx] = null;
            return null;
        }

        // ⑤/⑥ 衣服/裤子：真正的 Farmer.Equip(真件, 槽位NetRef) + 覆盖值设"-1"。
        //   ⚠️ 不能用 changeShirt/changePantStyle（那只是设覆盖值=默认色id、不碰 shirtItem → 丢染色+host画默认）。
        //   覆盖值只要不是"-1"就 getDisplay 优先用它盖过 shirtItem → 必须设"-1"让渲染走真件（含染色）。
        if (item is StardewValley.Objects.Clothing c)
        {
            var id = c.ItemId;
            bool isPants = Game1.pantsData.ContainsKey(id);
            if (!isPants && !Game1.shirtData.ContainsKey(id))
                return $"无法识别衣服 '{item.Name}'（不在 shirtData/pantsData）";
            slot = isPants ? "pants" : "shirt";
            var slotRef = isPants ? farmer.pantsItem : farmer.shirtItem;
            var old = farmer.Equip(c, slotRef);
            if (isPants) farmer.pants.Value = "-1"; else farmer.shirt.Value = "-1";
            farmer.FarmerRenderer?.MarkSpriteDirty();
            if (old != null) { if (!farmer.addItemToInventoryBool(old)) return "背包满，装不下旧衣服"; replaced = old.Name; }
            farmer.Items[idx] = null;
            return null;
        }

        return $"不支持的穿戴物类型: {item.Name}（需 衣服/裤子/帽/鞋/戒指/饰品）";
    }

    /// <summary>把某槽卸下（用游戏原生 Farmer.Equip(null, slotNetRef)，自动 onUnequip / 卸载 buff 效果）。
    /// 衣服/裤子不建议脱空（画面走覆盖值会怪），用 wear(name=另一件) 替换。成功返回 true。</summary>
    private bool TryTakeOff(Farmer farmer, string slot, out string? removed, out string? error)
    {
        removed = null; error = null;
        Item? old = null;
        // 戒指槽别名归一（wear slot=1/left/左手 等价 leftRing；2/right/右手 等价 rightRing）
        slot = slot switch { "1" or "left" or "左手" => "leftRing", "2" or "right" or "右手" => "rightRing", _ => slot };
        switch (slot)
        {
            case "boots":
                old = farmer.Equip(null, farmer.boots); break;
            case "leftRing":
                old = farmer.Equip(null, farmer.leftRing); break;
            case "rightRing":
                old = farmer.Equip(null, farmer.rightRing); break;
            case "hat":
                old = farmer.Equip(null, farmer.hat); break;
            case "trinket":
                old = farmer.trinketItems.FirstOrDefault();
                if (old != null) farmer.trinketItems.Clear();   // NetList 事件自动卸载效果
                break;
            case "shirt":
            case "pants":
                error = "衣服/裤子用 wear(name=另一件) 替换即可，不提供脱空";
                return false;
            default:
                error = $"未知槽位 '{slot}'（boots/leftRing/rightRing/trinket/hat/shirt/pants）";
                return false;
        }
        if (old == null) { error = $"没穿 {slot}"; return false; }
        if (!farmer.addItemToInventoryBool(old)) { error = "背包满，装不下"; return false; }
        removed = old.Name;
        return true;
    }

    /// <summary>是否为 1.6 饰品（Trinket）：引用 DLL 可能缺失该类型，按类型名兜底判断。</summary>
    private static bool IsTrinket(Item i)
    {
        var t = i.GetType();
        return t.Name == "Trinket" || t.FullName?.Contains(".Trinket") == true;
    }

    /// <summary>解析 hand 参数 → "left"/"right" 或 null(未指定/未知)。支持 1/left/左手(左)、2/right/右手(右)，
    /// 或某个当前已戴戒指的名字（自动定位它在哪只手，左手优先）。未知/空 → 交回自动（左空则左，否则右）。</summary>
    private static string? ResolveRingHand(Farmer farmer, string hand)
    {
        hand = hand.Trim().ToLowerInvariant();
        if (hand.Length == 0 || hand == "auto") return null;
        if (hand is "1" or "left" or "leftring" or "左手") return "left";
        if (hand is "2" or "right" or "rightring" or "右手") return "right";
        if (farmer.leftRing?.Value?.Name?.Equals(hand, StringComparison.OrdinalIgnoreCase) == true) return "left";
        if (farmer.rightRing?.Value?.Name?.Equals(hand, StringComparison.OrdinalIgnoreCase) == true) return "right";
        return null;
    }

    /// <summary>
    /// GET /worn — 查询全部穿戴物：衣服/裤子/帽子/捏人饰品、鞋子+属性、左右戒指+属性、饰品+属性。
    /// </summary>
    private object HandleWorn()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");
        var farmer = Game1.player;
        var worn = new Dictionary<string, object?>
        {
            ["shirt"] = ItemName(farmer.shirtItem),
            ["pants"] = ItemName(farmer.pantsItem),
            ["hat"] = ItemName(farmer.hat),
            ["accessory"] = DescribeAccessory(farmer),
            ["boots"] = farmer.boots?.Value != null
                ? new { name = farmer.boots.Value.Name, defense = farmer.boots.Value.defenseBonus.Value, immunity = farmer.boots.Value.immunityBonus.Value }
                : null,
            ["leftRing"] = DescribeRing(farmer.leftRing?.Value),
            ["rightRing"] = DescribeRing(farmer.rightRing?.Value),
            ["trinket"] = DescribeTrinket(farmer.trinketItems.FirstOrDefault())
        };
        return new { ok = true, worn };
    }

    private object? DescribeRing(StardewValley.Objects.Ring? ring)
    {
        if (ring == null) return null;
        if (ring is StardewValley.Objects.CombinedRing)
        {
            // 反射读合成的两个内环（字段名版本差异，遍历可枚举字段）
            var inner = new List<string>();
            try
            {
                foreach (var f in ring.GetType().GetFields(
                    System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance))
                {
                    var val = f.GetValue(ring);
                    if (val is System.Collections.IEnumerable en && !(val is string))
                    {
                        foreach (var r in en)
                            if (r is StardewValley.Objects.Ring rr && rr.Name != null)
                                inner.Add(rr.Name);
                    }
                }
            }
            catch { }
            return new { name = ring.Name, combined = inner };
        }
        return new { name = ring.Name };
    }

    private object? DescribeTrinket(Item? trinket)
    {
        if (trinket == null) return null;
        var entry = new Dictionary<string, object?> { ["name"] = trinket.Name };
        try
        {
            // 找 effect 对象：TrinketEffect 属性/字段，或任意非原始类型的字段
            object? effect = trinket.GetType().GetProperty("TrinketEffect",
                System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance)?.GetValue(trinket)
                ?? trinket.GetType().GetField("TrinketEffect",
                    System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance)?.GetValue(trinket);
            if (effect == null)
            {
                foreach (var f in trinket.GetType().GetFields(
                    System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance))
                {
                    var v = f.GetValue(trinket);
                    if (v != null && !f.FieldType.IsPrimitive && f.FieldType != typeof(string))
                    {
                        effect = v;
                        break;
                    }
                }
            }
            if (effect != null)
            {
                var props = new Dictionary<string, object?>();
                foreach (var f in effect.GetType().GetFields(
                    System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance))
                {
                    try { props[f.Name] = f.GetValue(effect); } catch { }
                }
                foreach (var pr in effect.GetType().GetProperties(
                    System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance))
                {
                    try { props["p_" + pr.Name] = pr.GetValue(effect); } catch { }
                }
                entry["effect"] = props;
            }
        }
        catch { }
        return entry;
    }

    private object? DescribeAccessory(Farmer farmer)
    {
        try
        {
            // 捏人饰品（面部饰品）：farmer.accessory
            var f = typeof(Farmer).GetField("accessory",
                System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
            return ItemName(f?.GetValue(farmer));
        }
        catch { return null; }
    }

    /// <summary>
    /// 安全取穿戴物名字：兼容 NetRef（取 Value），过滤占位符 "Character (Farmer): xxx"。
    /// </summary>
    private string? ItemName(object? item)
    {
        if (item == null) return null;
        try
        {
            var val = item.GetType().GetProperty("Value",
                System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance)?.GetValue(item)
                ?? item;
            var name = val.GetType().GetProperty("Name")?.GetValue(val)?.ToString();
            if (name != null && name.StartsWith("Character (Farmer)")) return null;
            return name;
        }
        catch { return null; }
    }

    private object HandleSurroundings(HttpListenerContext ctx)
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var qs = ctx.Request.QueryString;
        int radius = 10;
        if (int.TryParse(qs["radius"], out var r) && r > 0 && r <= 30)
            radius = r;

        // ⚠️ 2026-08-10: isTilePassable/objects 等地图状态必须主线程读——HTTP线程读火山图
        //    全报不可走（连玩家脚下都 False，AI 炸矿找锚点直接废）。包 EnqueueMainThread
        //    （同 /dump_tile 已验证的主线程模式）彻底解决。
        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try { tcs.SetResult(BuildSurroundings(radius)); }
            catch (Exception ex) { tcs.SetResult(new { ok = false, error = ex.Message }); }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    private object BuildSurroundings(int radius)
    {
        var farmer = Game1.player;
        var loc = farmer.currentLocation;
        var cx = farmer.TilePoint.X;
        var cy = farmer.TilePoint.Y;

        var tiles = new List<object>();

        for (int dy = -radius; dy <= radius; dy++)
        {
            for (int dx = -radius; dx <= radius; dx++)
            {
                int tx = cx + dx, ty = cy + dy;
                if (tx < 0 || ty < 0) continue;
                var mapW = loc.Map.DisplayWidth / 64;
                var mapH = loc.Map.DisplayHeight / 64;
                if (tx >= mapW || ty >= mapH) continue;

                var tileVec = new Vector2(tx, ty);
                var passable = loc.isTilePassable(tileVec);

                string? objName = null;
                string? objId = null;
                if (loc.objects.TryGetValue(tileVec, out var obj))
                {
                    objName = SafeObjectName(obj);
                    objId = obj.QualifiedItemId ?? obj.itemId?.Value;
                }

                string? terrainName = null;
                // ⚠️ 2026-08-15 修：diggable 对齐游戏 till 判定（makeHoeDirt: Diggable 且 !IsTileBlockedBy(忽略人物)）——
                //    只查 Diggable 会把"有物体/墙挡着"的格误报可耕（山地农场草皮/山崖），plot_plan 耕了失败。
                bool diggable = loc.doesTileHaveProperty(tx, ty, "Diggable", "Back") != null
                    && !loc.IsTileBlockedBy(tileVec, ~(StardewValley.CollisionMask.Characters | StardewValley.CollisionMask.Farmers));
                bool watered = false;
                string? cropName = null;
                string? forageCropType = null;   // 🫚 2026-08-17：crop.forageCrop 类型（"2"=姜，锄出）
                bool treeHasMoss = false;        // 🌿 2026-08-17：绿雨树长苔藓（Tree.hasMoss，可收 Moss）
                bool treeTempGreenRain = false;  // 🌿 临时绿雨树
                int cropPhase = -1;
                bool harvestable = false;
                string? fertStr = null;   // 化肥 item ID 字符串（SDV1.6 HoeDirt.fertilizer 是 NetString，如 "368"/"(O)368"）— 撒化肥/检测兜底用

                if (loc.terrainFeatures.TryGetValue(tileVec, out var tf))
                {
                    terrainName = tf.GetType().Name;
                    if (tf is HoeDirt dirt)
                    {
                        terrainName = "HoeDirt";
                        watered = dirt.state.Value == 1;
                        fertStr = dirt.fertilizer.Value;
                        if (dirt.crop != null)
                        {
                            cropName = dirt.crop.indexOfHarvest.Value;
                            cropPhase = dirt.crop.currentPhase.Value;
                            harvestable = dirt.readyForHarvest();
                            // 🫚 forageCrop（春葱/姜）：锄地出（crop.hitWithHoe）。类型 "2"=姜（Ginger）
                            if (dirt.crop.forageCrop.Value)
                                forageCropType = dirt.crop.whichForageCrop.Value;
                        }
                    }
                    else if (tf is Tree tree)
                    {
                        terrainName = $"Tree:{tree.treeType.Value}";
                        treeHasMoss = tree.hasMoss.Value;                     // 🌿 10/11/12=绿雨树，hasMoss=可收苔藓
                        treeTempGreenRain = tree.isTemporaryGreenRainTree.Value;
                    }
                    else if (tf is GiantCrop gc)
                    {
                        terrainName = "GiantCrop";
                    }
                }

                string? resourceName = null;
                var clump = loc.resourceClumps.FirstOrDefault(c =>
                    c.Tile == tileVec || (tx >= c.Tile.X && tx < c.Tile.X + c.width.Value
                    && ty >= c.Tile.Y && ty < c.Tile.Y + c.height.Value));
                if (clump != null)
                    resourceName = clump.parentSheetIndex.Value switch
                    {
                        600 => "LargeStump",
                        602 => "LargeLog",
                        622 => "MeteoriteOre",
                        672 => "LargeBoulder",
                        752 => "LargeBoulder",
                        754 => "LargeBoulder",
                        _ => $"Clump:{clump.parentSheetIndex.Value}"
                    };

                // 🍓 灌木丛（2026-08-17 恒+克劳德：Bush 不在 terrainFeatures，在 largeTerrainFeatures！）
                string? largeTerrainName = null;
                bool bushInBloom = false;
                foreach (var ltf in loc.largeTerrainFeatures)
                {
                    if (ltf.Tile == tileVec)
                    {
                        largeTerrainName = ltf.GetType().Name;
                        if (ltf is Bush bush)
                        {
                            largeTerrainName = "Bush";
                            // ⚠️ 2026-08-17 恒实测：inBloom() 只按季节判断（spring15-18 全灌木 true），
                            //    真正结果（有莓果可摇）看 tileSheetOffset==1（贴图=果）。Backwoods 11棵里只有3棵结果。
                            bushInBloom = bush.tileSheetOffset.Value == 1;
                        }
                        break;
                    }
                }

                bool hasInfo = !passable || objName != null || terrainName != null
                    || largeTerrainName != null || resourceName != null || diggable || cropName != null;
                if (hasInfo)
                {
                    var tileTerrain = terrainName ?? largeTerrainName;
                    var tile = new Dictionary<string, object?> { ["x"] = tx, ["y"] = ty, ["passable"] = passable };
                    if (diggable) tile["diggable"] = true;
                    if (objName != null) tile["object"] = objName;
                    if (objId != null) tile["objId"] = objId;
                    if (tileTerrain != null) tile["terrain"] = tileTerrain;
                    if (bushInBloom) tile["bushBloom"] = true;   // 🍓 灌木在花期=可摇树莓/黑莓
                    if (largeTerrainName != null && largeTerrainName != "Bush")
                        tile["largeTerrain"] = largeTerrainName;
                    if (resourceName != null) tile["resource"] = resourceName;
                    // ⚠️ 2026-08-17：大葱 cropName(indexOfHarvest) 为空但 forageCropType="1" 非空——
                    //    harvestable/cropPhase 不能只跟 cropName 走（否则成熟大葱漏报 harvestable）
                    if (cropName != null) tile["crop"] = cropName;
                    if (cropName != null || forageCropType != null)
                    {
                        tile["cropPhase"] = cropPhase;
                        tile["harvestable"] = harvestable;
                    }
                    if (forageCropType != null) tile["forageCrop"] = forageCropType;   // "1"=大葱(摘) "2"=姜(锄)
                    if (treeHasMoss) tile["moss"] = true;             // 🌿 树长苔藓（可收 Moss）
                    if (treeTempGreenRain) tile["greenRainTree"] = true;
                    if (watered) tile["watered"] = true;
                    if (!string.IsNullOrEmpty(fertStr)) tile["fert"] = fertStr;
                    tiles.Add(tile);
                }
            }
        }

        var nearbyNpcs = loc.characters
            .Where(n => !(n is Monster) && Math.Abs(n.TilePoint.X - cx) <= radius && Math.Abs(n.TilePoint.Y - cy) <= radius)
            .Select(n => new {
                name = n.Name,
                x = n.TilePoint.X,
                y = n.TilePoint.Y,
                // ⚠️ 2026-08-16：kind=pet/horse/npc——pet_pet 自然走摸猫狗用（/surroundings 报 NPC 类型）
                kind = n is Pet ? "pet" : n is Horse ? "horse" : "npc"
            })
            .ToList();

        var nearbyMonsters = loc.characters
            .OfType<Monster>()
            .Where(m => Math.Abs(m.TilePoint.X - cx) <= radius && Math.Abs(m.TilePoint.Y - cy) <= radius)
            .Select(m => new { name = m.Name, x = m.TilePoint.X, y = m.TilePoint.Y, health = m.Health, maxHealth = m.MaxHealth })
            .ToList();

        var nearbyFarmers = Game1.getOnlineFarmers()
            .Where(f => f != farmer && f.currentLocation == loc
                && Math.Abs(f.TilePoint.X - cx) <= radius && Math.Abs(f.TilePoint.Y - cy) <= radius)
            .Select(f => new { name = f.Name, x = f.TilePoint.X, y = f.TilePoint.Y })
            .ToList();

        return new
        {
            ok = true,
            center = new { x = cx, y = cy },
            radius,
            location = loc.Name,
            tiles,
            npcs = nearbyNpcs,
            monsters = nearbyMonsters,
            farmers = nearbyFarmers
        };
    }

    /// <summary>
    /// POST /appearance  { "hair": 0, "hairColor": "FF8800", "skin": 0, "shirt": 1000, ... }
    /// Hot-change the farmer's appearance at runtime.
    /// All fields optional — only provided ones are changed.
    ///
    /// Numeric fields: hair (0-?), skin (0-?), shirt (1000-?), pants (1000-?), acc (0-?)
    /// Color fields: hex string "RRGGBB" or "AARRGGBB" (with or without # prefix)
    /// </summary>
    private object HandleAppearance(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);

        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var farmer = Game1.player;
                var changed = new List<string>();

                // ⚠️ 2026-08-15 恒：必须用 SDV 官方 change* 方法（CharacterCustomization 同款）改外观。
                // 它们同时更新 Farmer 字段 + FarmerRenderer 字段（两者都是联机同步的 Net 字段），host 才渲染正确。
                // 之前直接写 farmer.shirtItem/skin/hair 单字段导致联机贴图错乱，原因：
                //   ① 1.6 的 farmer.shirt/pants 是 NetString "覆盖值"（创建角色时定），GetDisplayShirt 优先用它盖过 shirtItem——
                //      只改 shirtItem 不改覆盖值 → host 一直画旧衣服（单机测试角色已装备过衣服、覆盖值=-1 所以碰巧没暴露）；
                //   ② FarmerRenderer 自带的 skin/eyes/shirt/pants Net 字段没跟着改 → host 端 recolor 用陈旧值 → 颜色/袖子错乱。

                // ── Hair style ──
                if (p.TryGetValue("hair", out var hairVal) && hairVal is JsonElement hairJe && hairJe.ValueKind == JsonValueKind.Number)
                {
                    farmer.changeHairStyle(hairJe.GetInt32());
                    changed.Add("hair");
                }

                // ── Hair color ──
                if (p.TryGetValue("hairColor", out var hcVal) && hcVal is JsonElement hcJe && hcJe.ValueKind == JsonValueKind.String)
                {
                    var c = ParseColor(hcJe.GetString()!);
                    if (c.HasValue) { farmer.changeHairColor(c.Value); changed.Add("hairColor"); }
                }

                // ── Skin index (0-23) ──
                if (p.TryGetValue("skin", out var skinVal) && skinVal is JsonElement skinJe && skinJe.ValueKind == JsonValueKind.Number)
                {
                    farmer.changeSkinColor(skinJe.GetInt32());
                    changed.Add("skin");
                }

                // ── Shirt (itemId, e.g. 1000) — changeShirt 走 NetString 覆盖值，联机同步可靠 ──
                if (p.TryGetValue("shirt", out var shirtVal) && shirtVal is JsonElement shirtJe && shirtJe.ValueKind == JsonValueKind.Number)
                {
                    var shirtId = shirtJe.GetInt32().ToString();
                    if (Game1.shirtData.ContainsKey(shirtId))
                    {
                        farmer.changeShirt(shirtId);
                        changed.Add("shirt");
                    }
                }

                // ── Pants (itemId) ──
                if (p.TryGetValue("pants", out var pantsVal) && pantsVal is JsonElement pantsJe && pantsJe.ValueKind == JsonValueKind.Number)
                {
                    var pantsId = pantsJe.GetInt32().ToString();
                    if (Game1.pantsData.ContainsKey(pantsId))
                    {
                        farmer.changePantStyle(pantsId);
                        changed.Add("pants");
                    }
                }

                // ── Accessory (0-29) ──
                if (p.TryGetValue("acc", out var accVal) && accVal is JsonElement accJe && accJe.ValueKind == JsonValueKind.Number)
                {
                    farmer.changeAccessory(accJe.GetInt32());
                    changed.Add("acc");
                }

                // ── Eye color ──
                if (p.TryGetValue("eyeColor", out var ecVal) && ecVal is JsonElement ecJe && ecJe.ValueKind == JsonValueKind.String)
                {
                    var c = ParseColor(ecJe.GetString()!);
                    if (c.HasValue) { farmer.changeEyeColor(c.Value); changed.Add("eyeColor"); }
                }

                // ── Pants color ──
                if (p.TryGetValue("pantsColor", out var pcVal) && pcVal is JsonElement pcJe && pcJe.ValueKind == JsonValueKind.String)
                {
                    var c = ParseColor(pcJe.GetString()!);
                    if (c.HasValue) { farmer.changePantsColor(c.Value); changed.Add("pantsColor"); }
                }

                // ── Hat (index, 0-93) ──
                if (p.TryGetValue("hat", out var hatVal) && hatVal is JsonElement hatJe && hatJe.ValueKind == JsonValueKind.Number)
                {
                    try
                    {
                        farmer.changeHat(hatJe.GetInt32());
                        changed.Add("hat");
                    }
                    catch { /* bad index, skip */ }
                }

                // Refresh sprite（change* 内部已触发 renderer dirty，这里再兜底一次）
                farmer.FarmerRenderer?.MarkSpriteDirty();

                tcs.SetResult(new { ok = true, changed });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// GET /appearance_info
    /// Returns current appearance with item names/descriptions.
    /// </summary>
    private object HandleAppearanceInfo()
    {
        if (!Context.IsWorldReady)
            return new { ok = false, error = "World not ready" };

        try
        {
            var farmer = Game1.player;

            // 报"实际显示"的穿戴（2026-08-15）：1.6 用 GetShirtId/GetPantsId 读展示用 ID——
            // 可能是 shirt/pants 覆盖值（set_appearance 走 changeShirt 后是这个），也可能是已装备 item。
            var shirtInfo = (object?)null;
            var shirtId = farmer.GetShirtId();
            if (!string.IsNullOrEmpty(shirtId) && ItemRegistry.Exists("(S)" + shirtId))
            {
                var shirtItem = ItemRegistry.Create("(S)" + shirtId);
                if (shirtItem != null)
                {
                    shirtInfo = new
                    {
                        name = shirtItem.DisplayName,
                        description = shirtItem.getDescription()
                    };
                }
            }

            var pantsInfo = (object?)null;
            var pantsId = farmer.GetPantsId();
            if (!string.IsNullOrEmpty(pantsId) && ItemRegistry.Exists("(P)" + pantsId))
            {
                var pantsItem = ItemRegistry.Create("(P)" + pantsId);
                if (pantsItem != null)
                {
                    pantsInfo = new
                    {
                        name = pantsItem.DisplayName,
                        description = pantsItem.getDescription()
                    };
                }
            }

            var hatInfo = (object?)null;
            if (farmer.hat.Value != null)
            {
                hatInfo = new
                {
                    name = farmer.hat.Value.DisplayName,
                    description = farmer.hat.Value.getDescription()
                };
            }

            var bootsInfo = (object?)null;
            if (farmer.boots.Value != null)
            {
                bootsInfo = new
                {
                    name = farmer.boots.Value.DisplayName,
                    description = farmer.boots.Value.getDescription()
                };
            }

            return new
            {
                ok = true,
                name = farmer.Name,
                favoriteThing = farmer.favoriteThing.Value,
                hair = new { index = farmer.hair.Value },
                hairColor = new { hex = ColorToHex(farmer.hairstyleColor.Value) },
                skin = new { index = farmer.skin.Value },
                newEyeColor = new { hex = ColorToHex(farmer.newEyeColor.Value) },
                pantsColor = new { hex = ColorToHex(farmer.pantsColor.Value) },
                accessory = new { index = farmer.accessory.Value },
                shirt = shirtInfo,
                pants = pantsInfo,
                hat = hatInfo,
                boots = bootsInfo
            };
        }
        catch (Exception ex)
        {
            return new { ok = false, error = ex.Message };
        }
    }

    /// <summary>
    /// GET /appearance_ref
    /// Dump ALL customization-visible shirts + pants (id, display name, description) straight from the
    /// running game's own data — 非反编译，走 DataLoader 内容。供 MCP 把 set_appearance 参考表烤全（中文名 + 游戏描述）。
    /// </summary>
    private object HandleAppearanceRef()
    {
        if (!Context.IsWorldReady)
            return new { ok = false, error = "World not ready" };

        try
        {
            var shirts = DumpAppearanceItems(Game1.shirtData.Keys, "(S)");
            var pants = DumpAppearanceItems(Game1.pantsData.Keys, "(P)");
            return new { ok = true, shirts, pants };
        }
        catch (Exception ex)
        {
            return new { ok = false, error = ex.Message };
        }
    }

    /// <summary>按 Game1.*Data 的 key 循环，用 ItemRegistry 造物品拿 DisplayName+getDescription（沿用 /appearance_info 取描述路子）。</summary>
    private object[] DumpAppearanceItems(IEnumerable<string> ids, string prefix)
    {
        var map = new SortedDictionary<int, object>();
        foreach (var raw in ids)
        {
            if (!int.TryParse(raw, out var idNum)) continue;
            var item = ItemRegistry.Create($"{prefix}{raw}");
            if (item == null) continue;
            var desc = "";
            try { desc = item.getDescription(); } catch { }
            map[idNum] = new { id = idNum, name = item.DisplayName, description = desc };
        }
        return map.Values.ToArray();
    }

    /// <summary>
    /// GET /alerts ?peek=true
    /// Returns queued game/system alerts. By default this drains the queue.
    /// </summary>
    private object HandleAlerts(HttpListenerContext ctx)
    {
        var qs = ctx.Request.QueryString;
        bool peek = bool.TryParse(qs["peek"], out var p) && p;

        lock (_alertLock)
        {
            var alerts = _alertQueue.ToList();
            if (!peek)
                _alertQueue.Clear();

            return new
            {
                ok = true,
                count = alerts.Count,
                alerts
            };
        }
    }

    /// <summary>
    /// POST /face  { "direction": 2 }
    /// Sets the farmer's facing direction. 0=up, 1=right, 2=down, 3=left
    /// </summary>
    private object HandleFace(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var dir = GetParam<int>(p, "direction");
        if (dir < 0 || dir > 3)
            throw new InvalidOperationException("direction must be 0-3 (up/right/down/left)");

        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            Game1.player.FacingDirection = dir;
            tcs.SetResult(new { ok = true, direction = dir });
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// POST /select  { "name": "Parsnip Seeds" }
    /// Selects an inventory item by name (sets it as the active toolbar slot).
    /// </summary>
    private object HandleSelect(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var name = GetParam<string>(p, "name");

        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            var farmer = Game1.player;
            var idx = -1;
            // 精确匹配优先（Name/DisplayName）——避免 "Salad" 误匹配 "Fruit Salad"（2026-08-08 修复）
            for (int i = 0; i < farmer.Items.Count; i++)
            {
                if (farmer.Items[i] != null &&
                    (farmer.Items[i].Name.Equals(name, StringComparison.OrdinalIgnoreCase)
                     || farmer.Items[i].DisplayName.Equals(name, StringComparison.OrdinalIgnoreCase)))
                {
                    idx = i;
                    break;
                }
            }
            // 精确没有 → Contains 兜底（工具等级名："Pickaxe"→"Iridium Pickaxe"）
            if (idx < 0)
            {
                for (int i = 0; i < farmer.Items.Count; i++)
                {
                    if (farmer.Items[i] != null &&
                        (farmer.Items[i].Name ?? "").Contains(name, StringComparison.OrdinalIgnoreCase))
                    {
                        idx = i;
                        break;
                    }
                }
            }

            if (idx < 0)
            {
                tcs.SetResult(new { ok = false, error = $"Item '{name}' not found in inventory" });
                return;
            }

            farmer.CurrentToolIndex = idx;
            tcs.SetResult(new { ok = true, selected = name, slot = idx });
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// POST /use  { "force": false }
    /// Uses the currently held item with pre-validation.
    /// Tools: checks if facing tile is appropriate (hoe→diggable empty, wateringcan→HoeDirt, axe→tree/stump, pickaxe→stone).
    /// Placeables: checks tile is clear. Pass force=true to skip validation.
    /// </summary>
    private object HandleUse(HttpListenerContext ctx)
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var p = ReadJson(ctx);
        var force = GetParamOr(p, "force", false);

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            var farmer = Game1.player;
            var item = farmer.CurrentItem;
            if (item == null)
            {
                tcs.SetResult(new { ok = false, error = "No item selected" });
                return;
            }

            var facingTile = GetFacingTile(farmer);
            var loc = farmer.currentLocation;
            int ftx = (int)facingTile.X, fty = (int)facingTile.Y;
            var tileVec = new Vector2(ftx, fty);

            if (item is Tool tool && !force)
            {
                var validation = ValidateToolUse(tool, loc, tileVec, ftx, fty);
                if (validation != null)
                {
                    tcs.SetResult(new { ok = false, error = validation,
                        tile = new { x = ftx, y = fty }, tool = tool.Name });
                    return;
                }
            }

            if (item is Tool)
            {
                farmer.BeginUsingTool();
                tcs.SetResult(new { ok = true, action = "tool", item = item.Name,
                    tile = new { x = ftx, y = fty } });
            }
            else if (item is StardewValley.Object obj)
            {
                int px = ftx * 64, py = fty * 64;
                bool placed = obj.placementAction(loc, px, py, farmer);
                if (placed)
                {
                    farmer.reduceActiveItemByOne();
                    tcs.SetResult(new { ok = true, action = "placed", item = item.Name,
                        tile = new { x = ftx, y = fty } });
                }
                else
                {
                    tcs.SetResult(new { ok = false, error = $"Cannot place '{item.Name}' here",
                        tile = new { x = ftx, y = fty } });
                }
            }
            else
            {
                tcs.SetResult(new { ok = false, error = $"Cannot use '{item.Name}' (unsupported item type)" });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    private string? ValidateToolUse(Tool tool, GameLocation loc, Vector2 tileVec, int tx, int ty)
    {
        bool hasObj = loc.objects.ContainsKey(tileVec);
        loc.terrainFeatures.TryGetValue(tileVec, out var tf);
        bool diggable = loc.doesTileHaveProperty(tx, ty, "Diggable", "Back") != null;

        switch (tool)
        {
            case Hoe:
                if (tf is HoeDirt)
                    return "Tile already tilled";
                if (hasObj)
                    return $"Tile blocked by object: {loc.objects[tileVec].Name}";
                if (!diggable)
                    return "Tile is not diggable";
                return null;

            case WateringCan:
                if (tf is not HoeDirt dirt)
                    return "No tilled soil here — till first";
                if (dirt.state.Value == 1)
                    return "Already watered";
                return null;

            case Axe:
                bool hasTree = tf is Tree;
                bool hasStump = loc.resourceClumps.Any(c =>
                    (c.parentSheetIndex.Value == 600 || c.parentSheetIndex.Value == 602)
                    && tx >= c.Tile.X && tx < c.Tile.X + c.width.Value
                    && ty >= c.Tile.Y && ty < c.Tile.Y + c.height.Value);
                bool hasTwig = hasObj && loc.objects[tileVec].Name == "Twig";
                if (!hasTree && !hasStump && !hasTwig)
                    return "Nothing to chop here";
                return null;

            case Pickaxe:
                bool hasStone = hasObj && loc.objects[tileVec].Name == "Stone";
                bool hasBoulder = loc.resourceClumps.Any(c =>
                    (c.parentSheetIndex.Value == 672 || c.parentSheetIndex.Value == 752 || c.parentSheetIndex.Value == 754 || c.parentSheetIndex.Value == 622)
                    && tx >= c.Tile.X && tx < c.Tile.X + c.width.Value
                    && ty >= c.Tile.Y && ty < c.Tile.Y + c.height.Value);
                if (!hasStone && !hasBoulder && tf is not HoeDirt)
                    return "Nothing to break here";
                return null;

            default:
                return null;
        }
    }

    /// <summary>
    /// GET /map
    /// Returns buildings, warps, NPCs, and other farmers for the current location.
    /// Provides everything needed for long-range pathfinding and navigation.
    /// </summary>
    private object HandleMap()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            var farmer = Game1.player;
            var loc = farmer.currentLocation;
            var mapWidth = loc.Map.DisplayWidth / 64;
            var mapHeight = loc.Map.DisplayHeight / 64;

            // Buildings (Farm, etc.)
            var buildings = new List<object>();
            if (loc is Farm farm)
            {
                foreach (var b in farm.buildings)
                {
                    var entry = new Dictionary<string, object?>
                    {
                        ["type"] = b.buildingType.Value,
                        ["x"] = b.tileX.Value,
                        ["y"] = b.tileY.Value,
                        ["width"] = b.tilesWide.Value,
                        ["height"] = b.tilesHigh.Value
                    };
                    if (b.humanDoor.Value != Point.Zero || b.humanDoor.Value != default)
                    {
                        entry["doorX"] = b.tileX.Value + b.humanDoor.X;
                        entry["doorY"] = b.tileY.Value + b.humanDoor.Y;
                    }
                    buildings.Add(entry);
                }
            }

            // Warps (exits/entrances to other maps)
            var warps = loc.warps
                .Select(w => new
                {
                    x = w.X,
                    y = w.Y,
                    targetLocation = w.TargetName,
                    targetX = w.TargetX,
                    targetY = w.TargetY
                }).ToList();

            // All NPCs in current location
            var npcs = loc.characters
                .Select(n => new
                {
                    name = n.Name,
                    x = n.TilePoint.X,
                    y = n.TilePoint.Y
                }).ToList();

            // All other farmers in current location
            var farmers = Game1.getOnlineFarmers()
                .Where(f => f != farmer && f.currentLocation == loc)
                .Select(f => new
                {
                    name = f.Name,
                    x = f.TilePoint.X,
                    y = f.TilePoint.Y
                }).ToList();

            // Animals (if on farm or animal building interior)
            var animals = new List<object>();
            if (loc is Farm farmLoc)
            {
                foreach (var a in farmLoc.animals.Values)
                    animals.Add(new { name = a.Name, type = a.type.Value, x = a.TilePoint.X, y = a.TilePoint.Y });
            }
            else if (loc is AnimalHouse ah)
            {
                foreach (var a in ah.animals.Values)
                    animals.Add(new { name = a.Name, type = a.type.Value, x = a.TilePoint.X, y = a.TilePoint.Y });
            }

            tcs.SetResult(new
            {
                ok = true,
                player = new { x = farmer.TilePoint.X, y = farmer.TilePoint.Y },
                location = new
                {
                    name = loc.Name,
                    width = mapWidth,
                    height = mapHeight
                },
                buildings,
                warps,
                npcs,
                farmers,
                animals
            });
        });

        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// POST /buy  { "id": "472", "quantity": 5 }  or  { "id": "(O)472", "quantity": 5 }
    /// Buys an item: deducts gold, adds item to inventory.
    /// Optional "price" param to override per-unit cost; otherwise uses the item's default sale price * 2 (shop markup).
    /// </summary>
    private object HandleBuy(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var rawId = GetParam<string>(p, "id");
        var quantity = GetParamOr(p, "quantity", 1);
        var priceOverride = GetParamOr(p, "price", -1);

        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                // Qualify the item ID if needed (e.g. "472" -> "(O)472")
                var qualifiedId = rawId.StartsWith("(") ? rawId : ItemRegistry.QualifyItemId(rawId);
                if (qualifiedId == null)
                {
                    tcs.SetResult(new { ok = false, error = $"Unknown item ID: {rawId}" });
                    return;
                }

                // Create a test item to get its info
                var testItem = ItemRegistry.Create(qualifiedId, 1);
                if (testItem == null)
                {
                    tcs.SetResult(new { ok = false, error = $"Cannot create item: {qualifiedId}" });
                    return;
                }

                // Calculate price: override > default (salePrice * 2 as shop markup)
                int unitPrice = priceOverride >= 0
                    ? priceOverride
                    : (testItem is StardewValley.Object obj ? obj.salePrice() * 2 : 100);
                int totalCost = unitPrice * quantity;

                var farmer = Game1.player;
                if (farmer.Money < totalCost)
                {
                    tcs.SetResult(new { ok = false, error = $"Not enough gold. Need {totalCost}g, have {farmer.Money}g",
                        need = totalCost, have = farmer.Money });
                    return;
                }

                // Check inventory space
                int freeSlots = 0;
                for (int i = 0; i < farmer.MaxItems; i++)
                {
                    if (i >= farmer.Items.Count || farmer.Items[i] == null)
                        freeSlots++;
                }
                if (freeSlots < 1)
                {
                    tcs.SetResult(new { ok = false, error = "Inventory full! Please clear backpack before buying.",
                        freeSlots = 0 });
                    EnqueueAlert("inventory_full", "Cannot buy: inventory is full. Clear backpack first.", "warning", "buy");
                    return;
                }

                // Create the actual item and add to inventory
                var item = ItemRegistry.Create(qualifiedId, quantity);
                farmer.Money -= totalCost;
                farmer.addItemByMenuIfNecessary(item);

                tcs.SetResult(new
                {
                    ok = true,
                    bought = item.Name,
                    quantity,
                    unitPrice,
                    totalCost,
                    remainingGold = farmer.Money
                });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });

        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// POST /buy_animal
    /// Buy an animal from Marnie (bypasses PurchaseAnimalsMenu UI).
    /// Body: { animal_type, name, building? }
    /// </summary>
    private object HandleBuyAnimal(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var animalType = GetParam<string>(p, "animal_type");
        var name = GetParam<string>(p, "name");
        var buildingName = GetParamOr<string>(p, "building", "");

        if (string.IsNullOrEmpty(animalType) || string.IsNullOrEmpty(name))
            return new { ok = false, error = "Missing animal_type or name" };

        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var farmer = Game1.player;

                // Animal price & building requirement map
                var animalInfo = new Dictionary<string, (int price, string building)>(StringComparer.OrdinalIgnoreCase)
                {
                    ["White Chicken"] = (800, "Coop"),
                    ["Brown Chicken"] = (800, "Coop"),
                    ["Duck"] = (4000, "Big Coop"),
                    ["Rabbit"] = (8000, "Deluxe Coop"),
                    ["Cow"] = (1500, "Barn"),
                    ["Goat"] = (4000, "Big Barn"),
                    ["Sheep"] = (8000, "Deluxe Barn"),
                    ["Pig"] = (16000, "Deluxe Barn"),
                    ["Ostrich"] = (60000, "Barn"),
                    ["Golden Chicken"] = (50000, "Deluxe Coop"),
                };

                if (!animalInfo.ContainsKey(animalType))
                {
                    var valid = string.Join(", ", animalInfo.Keys);
                    tcs.SetResult(new { ok = false, error = $"Unknown animal type: '{animalType}'. Valid: {valid}" });
                    return;
                }

                var (price, requiredBuilding) = animalInfo[animalType];

                // Check money
                if (farmer.Money < price)
                {
                    tcs.SetResult(new { ok = false, error = $"Not enough gold. Need {price}g, have {farmer.Money}g",
                        need = price, have = farmer.Money });
                    return;
                }

                // Check inventory space (need at least 1 free slot for some cases)
                int usedSlots = farmer.Items.Count(item => item != null);
                if (usedSlots >= farmer.MaxItems)
                {
                    tcs.SetResult(new { ok = false, error = "Inventory full! Clear backpack first." });
                    return;
                }

                // Find suitable building
                var farm = Game1.getFarm();
                Building? targetBuilding = null;
                string? buildingTypeMatched = null;

                if (!string.IsNullOrEmpty(buildingName))
                {
                    // Try matching by building name or type
                    targetBuilding = farm.buildings.FirstOrDefault(b =>
                        (b.buildingType.Value?.IndexOf(buildingName, StringComparison.OrdinalIgnoreCase) >= 0) ||
                        (b.GetIndoorsName()?.IndexOf(buildingName, StringComparison.OrdinalIgnoreCase) >= 0));
                    if (targetBuilding != null) buildingTypeMatched = targetBuilding.buildingType.Value;
                }

                if (targetBuilding == null)
                {
                    // Auto-find: prefer a building that matches the requirement and has space
                    foreach (var b in farm.buildings)
                    {
                        if (b == null) continue;
                        var bType = b.buildingType.Value ?? "";
                        // Check if this building type can house this animal
                        bool typeMatch = false;
                        if (requiredBuilding == "Coop")
                            typeMatch = bType.IndexOf("Coop", StringComparison.OrdinalIgnoreCase) >= 0;
                        else if (requiredBuilding == "Big Coop")
                            typeMatch = bType.IndexOf("Coop", StringComparison.OrdinalIgnoreCase) >= 0;
                        else if (requiredBuilding == "Deluxe Coop")
                            typeMatch = bType.IndexOf("Deluxe Coop", StringComparison.OrdinalIgnoreCase) >= 0
                                       || bType.IndexOf("Big Coop", StringComparison.OrdinalIgnoreCase) >= 0
                                       || bType.IndexOf("Coop", StringComparison.OrdinalIgnoreCase) >= 0;
                        else if (requiredBuilding == "Barn")
                            typeMatch = bType.IndexOf("Barn", StringComparison.OrdinalIgnoreCase) >= 0;
                        else if (requiredBuilding == "Big Barn")
                            typeMatch = bType.IndexOf("Barn", StringComparison.OrdinalIgnoreCase) >= 0;
                        else if (requiredBuilding == "Deluxe Barn")
                            typeMatch = bType.IndexOf("Deluxe Barn", StringComparison.OrdinalIgnoreCase) >= 0
                                       || bType.IndexOf("Big Barn", StringComparison.OrdinalIgnoreCase) >= 0
                                       || bType.IndexOf("Barn", StringComparison.OrdinalIgnoreCase) >= 0;

                        if (!typeMatch) continue;

                        if (b.indoors.Value is AnimalHouse house)
                        {
                            int limit = house.animalLimit.Value;
                            if (limit > 0 && house.animals.Count() >= limit)
                                continue;
                            targetBuilding = b;
                            buildingTypeMatched = bType;
                            break;
                        }
                    }
                }

                if (targetBuilding == null)
                {
                    tcs.SetResult(new { ok = false, error = $"No suitable building found for {animalType}. Need a {requiredBuilding} with free space." });
                    return;
                }

                var indoors = targetBuilding.indoors.Value as AnimalHouse;
                if (indoors == null)
                {
                    tcs.SetResult(new { ok = false, error = $"Building '{targetBuilding.buildingType.Value}' is not an animal house" });
                    return;
                }

                int currentAnimals = indoors.animals.Count();
                int animalLimit = indoors.animalLimit.Value;
                if (animalLimit > 0 && currentAnimals >= animalLimit)
                {
                    tcs.SetResult(new { ok = false, error = $"Building is full ({currentAnimals}/{animalLimit})" });
                    return;
                }

                // Create FarmAnimal
                var animal = new FarmAnimal(animalType, Game1.Multiplayer.getNewID(), farmer.UniqueMultiplayerID);
                animal.Name = name;
                animal.displayName = name;
                animal.home = targetBuilding;
                // Add to building's animal dictionary (SDV 1.6 API: animals is NetLongDictionary)
                indoors.animals.Add(animal.myID.Value, animal);

                // Deduct money
                farmer.Money -= price;

                string msg = $"🐔 购买了 {animalType}「{name}」花费 {price}g，安置在 {buildingTypeMatched}";
                EnqueueAlert("animal_bought", msg, "info", "buy_animal");
                AddRecentEvent("buy_animal", msg, Game1.ticks);

                tcs.SetResult(new
                {
                    ok = true,
                    animal_type = animalType,
                    name,
                    price,
                    building = buildingTypeMatched ?? targetBuilding.buildingType.Value,
                    building_name = targetBuilding.GetIndoorsName(),
                    total_animals = currentAnimals + 1,
                    animal_limit = animalLimit,
                    remaining_gold = farmer.Money
                });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// POST /sleep
    /// Warps the farmer to their bed and triggers sleep (end of day).
    /// POST /sleep  { "stay": true } → 已在床上（isInBed）则就地睡觉，不 warp 回家。
    ///   用于 AI 操作：crawl_bed 把 DS 弄到 host 床上后，go_sleep 就地过夜，DS 才会在 host 床上醒来。
    /// </summary>
    private object HandleSleep(HttpListenerContext ctx)
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var p = ReadJson(ctx);
        bool stay = GetParamOr(p, "stay", false);

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var farmer = Game1.player;

                // stay=true → 就地睡觉（不 warp 回家），DS 睡在哪张床就在哪张床醒来。
                // ⚠️ 2026-08-14 回归修复：原条件 `stay && farmer.isInBed` —— crawl_bed 设的 isInBed
                //    可能被游戏 tick 弹掉（床格碰撞），导致落到下面的 warp 回家分支，DS 天亮在自己小屋。
                //    现在 stay=true 无条件就地睡（go_sleep 已把角色带到目标床边），不再 warp 回家。
                if (stay)
                {
                    farmer.sleptInTemporaryBed.Value = false;
                    farmer.isInBed.Value = true;
                    farmer.currentLocation.answerDialogueAction("Sleep_Yes", Array.Empty<string>());
                    DelayedAction.functionAfterDelay(() => Game1.netReady.SetLocalReady("sleep", true), 1500);
                    tcs.SetResult(new { ok = true, action = "sleeping_in_place", home = farmer.currentLocation?.Name, inBed = farmer.isInBed.Value });
                    return;
                }

                // Find home: try homeLocation, then scan all locations for a cabin belonging to this farmer
                var homeName = farmer.homeLocation.Value;
                GameLocation homeLoc = null;
                if (!string.IsNullOrEmpty(homeName))
                    homeLoc = Game1.getLocationFromName(homeName);

                if (homeLoc == null)
                {
                    // Scan for cabin with this farmer's unique ID
                    foreach (var loc in Game1.locations)
                    {
                        if (loc is StardewValley.Locations.Cabin cabin && cabin.owner == farmer)
                        {
                            homeLoc = cabin;
                            homeName = cabin.Name;
                            break;
                        }
                    }
                }

                // Fallback to FarmHouse for host
                if (homeLoc == null)
                {
                    homeLoc = Game1.getLocationFromName("FarmHouse");
                    homeName = "FarmHouse";
                }

                if (homeLoc == null)
                {
                    tcs.SetResult(new { ok = false, error = "Cannot find home location" });
                    return;
                }

                var bedX = 10;
                var bedY = 6;

                var needsWarp = farmer.currentLocation.Name != homeLoc.Name;
                if (needsWarp)
                {
                    Game1.warpFarmer(homeName, bedX, bedY, false);
                }

                // Longer delay for farmhand warp sync
                var delay = needsWarp ? 3000 : 500;
                DelayedAction.functionAfterDelay(() =>
                {
                    var f = Game1.player;
                    f.isInBed.Value = true;
                    f.sleptInTemporaryBed.Value = false;
                    f.currentLocation.answerDialogueAction("Sleep_Yes", Array.Empty<string>());
                    // 程序化触发时 ReadyCheckDialog 无人交互，需手动上报 ready 给 host。
                    // 真人流程：ReadyCheckDialog 内部自动 SetLocalReady("sleep", true)，
                    // 我们程序化触发没有这一步，所以 host 一直不认可。延迟等 dialog 创建后补上。
                    DelayedAction.functionAfterDelay(() =>
                    {
                        // 1.6 正确 API：Game1.netReady 是 ReadySynchronizer。
                        // 真人流程：ReadyCheckDialog 内部调 SetLocalReady("sleep", true) 上报 ready 给 host。
                        // 我们程序化触发无 UI 交互，需手动补上这一步，host 才会认可并等所有玩家就绪。
                        Game1.netReady.SetLocalReady("sleep", true);
                    }, 1500);
                }, delay);

                tcs.SetResult(new { ok = true, action = "sleeping", home = homeName, bed = $"{bedX},{bedY}" });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// POST /wakeup
    /// After sleeping / new day, walks the farmer out of their cabin to the farm.
    /// Returns current location and position.
    /// </summary>
    private object HandleWakeup()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            var farmer = Game1.player;
            var loc = farmer.currentLocation;

            // Find any warp out of current indoor location
            var warp = loc.warps.FirstOrDefault();
            if (warp != null)
            {
                // Directly warp the farmer - more reliable than walking
                Game1.warpFarmer(warp.TargetName, warp.TargetX, warp.TargetY, false);
                tcs.SetResult(new
                {
                    ok = true,
                    action = "warped",
                    from = loc.Name,
                    target = warp.TargetName,
                    x = warp.TargetX,
                    y = warp.TargetY
                });
            }
            else
            {
                tcs.SetResult(new { ok = true, action = "already_outside", location = loc.Name,
                    x = farmer.TilePoint.X, y = farmer.TilePoint.Y });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// POST /crawl_bed  { "action": "locate" | "sleep" }
    /// 🎭 彩蛋：AI(DeepSeek) 爬房主(小恒)的床。
    ///   locate: 只定位房主床坐标 + 当前是否在床上，不动玩家（python 端据此判断爬/起）。
    ///   sleep:  自动切换——不在床上 = 躺上床 + 广播"爬上了床"；已在床上 = 起身 + 广播"偷偷溜下床"。
    ///           不触发 Sleep_Yes（不过夜不结束一天）。起身/躺多久的决策完全归 AI（python 端控制调用时机）。
    /// </summary>
    private object HandleCrawlBed(HttpListenerContext ctx)
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var p = ReadJson(ctx);
        var action = GetParamOr(p, "action", "locate");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                // 解析目标玩家：默认房主(小恒)，或按 player 名字指定（如 DS 自己的名字 → 睡自己小屋的床）。
                var targetPlayer = Game1.MasterPlayer;
                var targetName = GetParamOr(p, "player", "");
                if (!string.IsNullOrEmpty(targetName))
                {
                    var found = Game1.getAllFarmers().FirstOrDefault(f => f.Name == targetName);
                    if (found != null) targetPlayer = found;
                }
                var (bedLoc, bedX, bedY) = FindPlayerBed(targetPlayer);

                if (action == "locate")
                {
                    // 诊断：列出游戏里所有 farmer（含离线 farmhand），供选择彩蛋演员
                    var farmers = Game1.getAllFarmers()
                        .Select(f => new
                        {
                            name = f.Name,
                            isMain = f.IsMainPlayer,
                            home = f.homeLocation.Value ?? ""
                        }).ToList();

                    tcs.SetResult(new
                    {
                        ok = true,
                        action = "locate",
                        bed = new { location = bedLoc, x = bedX, y = bedY },
                        player2 = Game1.player.Name,
                        player = targetPlayer.Name,
                        isInBed = Game1.player.isInBed.Value,
                        // 🆕 2026-08-22 恒：当前场景唯一名，供 Python 判"床是否就在当前场景"
                        //   （小屋显示名 Cabin ≠ 室内唯一名 FarmHouse<guid>；/state.location.name 只有显示名，
                        //    此字段对齐 walk_to 的 NameOrUniqueName，才能可靠判同场景）。
                        curLoc = Game1.player.currentLocation?.NameOrUniqueName ?? Game1.player.currentLocation?.Name,
                        farmers
                    });
                    return;
                }

                if (action != "sleep")
                {
                    tcs.SetResult(new { ok = false, error = $"Unknown action: {action}" });
                    return;
                }

                var farmer = Game1.player;
                var aiName = GetParamOr(p, "player2", Game1.player.Name);
                var masterName = targetPlayer.Name;
                var homeName = farmer.homeLocation.Value;

                // 统一"爬床"：DS 需先到目标床边（AI 用 walk_to 带过来），确认进床 + 广播"爬床"。
                // ⚠️ 2026-08-14：地点名比较要兼容显示名/真实名——小屋 Name="Cabin" 但 NameOrUniqueName="FarmHouse<guid>"。
                var curLoc = farmer.currentLocation;
                bool locMatch = curLoc != null && (curLoc.Name == bedLoc || curLoc.NameOrUniqueName == bedLoc);
                bool nearBed = locMatch
                    && Math.Abs(farmer.TilePoint.X - bedX) <= 3
                    && Math.Abs(farmer.TilePoint.Y - bedY) <= 3;
                if (!nearBed)
                {
                    tcs.SetResult(new
                    {
                        ok = false,
                        error = $"{aiName} 不在{masterName}的床边，请先用 walk_to 把 {aiName} 带到床附近再调 crawl_bed sleep",
                        player2 = aiName,
                        player = masterName,
                        bed = new { location = bedLoc, x = bedX, y = bedY }
                    });
                    return;
                }

                // 在床边 → 确认进床 + 广播"爬床"（只有爬别人的床才提示，自己床不提示）
                // ⚠️ 2026-08-14 回归修复：撤销"挪到床格中央"的做法——08-01 实测教训是
                //    "warp/teleport 到床 tile 会被游戏 redirect 到门口"，必须让 DS 走(walk_to)到床边，
                //    然后只设 isInBed=True（人留在床旁边即可，游戏自会处理躺床）。挪位置反而触发 redirect。
                if (!farmer.isInBed.Value)
                {
                    farmer.isInBed.Value = true;
                }
                farmer.sleptInTemporaryBed.Value = false;
                if (targetPlayer != farmer)
                {
                    Broadcast($"<{aiName}>爬上了<{masterName}>的床！");
                }

                tcs.SetResult(new
                {
                    ok = true,
                    action = "crawled",
                    bed = new { location = bedLoc, x = bedX, y = bedY },
                    player2 = aiName,
                    player = masterName,
                    home = homeName
                });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// 找房主在 FarmHouse 的床。优先扫家具里的 BedFurniture，找不到 fallback (10,6)。
    /// </summary>
    private (int x, int y) FindMasterBed(GameLocation? home)
    {
        if (home != null)
        {
            try
            {
                var bed = home.furniture.OfType<StardewValley.Objects.BedFurniture>().FirstOrDefault();
                if (bed != null)
                    return ((int)bed.TileLocation.X, (int)bed.TileLocation.Y);
            }
            catch
            {
                // 版本兼容问题 → 走 fallback
            }
        }
        return (10, 6);
    }

    /// <summary>
    /// 找任意玩家的家（homeLocation）里的床。host(小恒)→FarmHouse；farmhand(DS)→他自己的 Cabin。
    /// 优先扫家具里的 BedFurniture，找不到 fallback (10,6)。
    /// </summary>
    private (string locName, int x, int y) FindPlayerBed(Farmer target)
    {
        var homeName = target.homeLocation.Value;
        GameLocation home = null;
        if (!string.IsNullOrEmpty(homeName))
            home = Game1.getLocationFromName(homeName);
        if (home == null && target.IsMainPlayer)
            home = Game1.getLocationFromName("FarmHouse");

        if (home != null)
        {
            try
            {
                var bed = home.furniture.OfType<StardewValley.Objects.BedFurniture>().FirstOrDefault();
                if (bed != null)
                {
                    // ⚠️ 2026-08-14 修复：必须用 homeName（真实地点名，如 FarmHouse183e6ea6-…），
                    //    不能用 home.Name（显示名 "Cabin"，Cabin 类继承 FarmHouse 但 Name 是显示名）。
                    //    用 "Cabin" 做 warp → host RequireLocation("Cabin") 找不到 → 联机崩。
                    return (homeName, (int)bed.TileLocation.X, (int)bed.TileLocation.Y);
                }
            }
            catch
            {
                // 版本兼容问题 → 走 fallback
            }
        }
        return (homeName ?? (target.IsMainPlayer ? "FarmHouse" : ""), 10, 6);
    }

    /// <summary>
    /// 解析传送（位置+坐标）：火山入口目标不可加载 → 火山内部 VolcanoDungeon0 的安全入口 (31,53)。
    /// 其他目标不可加载 → 映射位置名（坐标用 warp 自带）。
    /// </summary>
    private (string loc, int x, int y) ResolveWarp(string targetName, int tx, int ty)
    {
        var loc = ResolveWarpTarget(targetName);
        if (loc == "VolcanoDungeon0")
            return (loc, 31, 51);  // 火山安全入口（用户实测 2026-08-02：31,53 会触发出口回弹，31,51 安全）
        return (loc, tx, ty);
    }

    /// <summary>
    /// 解析传送目标：目标位置不可加载（如火山入口 VolcanoEntrance）时映射到实际可加载位置。
    /// </summary>
    private string ResolveWarpTarget(string targetName)
    {
        if (string.IsNullOrEmpty(targetName)) return targetName;
        try
        {
            if (Game1.getLocationFromName(targetName) != null) return targetName;
            // 火山入口特殊：不可加载 → 火山内部第0层
            if (targetName.Equals("VolcanoEntrance", StringComparison.OrdinalIgnoreCase))
                return "VolcanoDungeon0";
            // 通用兜底：试试加 "0"（火山层数命名 VolcanoDungeon0/1/...）
            if (Game1.getLocationFromName(targetName + "0") != null) return targetName + "0";
        }
        catch { }
        return targetName;
    }

    /// <summary>
    /// 找玩家自己小屋的门（Farm 外立面）。房主→Farmhouse 建筑；farmhand→对应 Cabin。
    /// "回家"导航用——到门口而不是直接进屋里（进屋容易跑偏）。
    /// </summary>
    private object? FindHomeDoor(Farmer farmer)
    {
        try
        {
            var farm = Game1.getLocationFromName("Farm");
            if (farm == null) return null;
            var home = farmer.homeLocation.Value;
            foreach (var b in farm.buildings)
            {
                bool isOwn;
                if (home == "FarmHouse")
                    isOwn = (b.buildingType.Value ?? "").Equals("Farmhouse", StringComparison.OrdinalIgnoreCase);
                else
                    isOwn = b.indoors?.Value != null
                        && (b.indoors.Value.NameOrUniqueName == home || b.indoors.Value.Name == home);
                if (isOwn && b.humanDoor.Value != Point.Zero)
                {
                    return new { location = "Farm", x = b.tileX.Value + b.humanDoor.X, y = b.tileY.Value + b.humanDoor.Y };
                }
            }
        }
        catch { }
        return null;
    }

    /// <summary>
    /// 爬床（躺下）：Sleep_Yes 驱动上床（isInBed 保持 + 躺姿） + 广播"爬上了床"。
    /// 不自动 confirm → 多人下进 ReadyCheckDialog 躺着等待，AI 决定真睡(go_sleep)或起身(再调 crawl_bed)。
    /// 手动 isInBed=true 会被游戏 tick 弹下床（躺不持久），必须走 Sleep_Yes 睡觉流程驱动。
    /// </summary>
    private void LieInBed(int bedX, int bedY, string aiName, string masterName)
    {
        var f = Game1.player;
        f.isInBed.Value = true;
        f.sleptInTemporaryBed.Value = false;
        f.currentLocation.answerDialogueAction("Sleep_Yes", Array.Empty<string>());
        Broadcast($"<{aiName}>爬上了<{masterName}>的床！");
    }

    /// <summary>
    /// 起身：取消睡觉(起身+关 ReadyCheckDialog) + 广播"偷偷溜下床"。不自动回屋，去哪由 AI 决定。
    /// </summary>
    private void LeaveBed(string aiName, string masterName)
    {
        Game1.player.isInBed.Value = false;
        if (Game1.activeClickableMenu is StardewValley.Menus.ReadyCheckDialog rcd)
            rcd.exitThisMenu();
        Game1.activeClickableMenu = null;
        Game1.dialogueUp = false;
        Broadcast($"<{aiName}>偷偷溜下了<{masterName}>的床！");
    }

    /// <summary>
    /// 多人广播提示：走聊天通道同步到所有玩家（房主小恒也能看到），否则只有本进程可见。
    /// farmhand 进程还要额外推送到 host 进程(7842)/chat——Game1.chatBox.addMessage 只在调用进程本地显示，
    /// 跨进程同步不可靠，必须让 host 进程自己也 addMessage 一次，小恒窗口才看得到。
    /// </summary>
    private void Broadcast(string msg)
    {
        Game1.chatBox?.addMessage(msg, Color.HotPink);
        if (Context.IsMultiplayer)
        {
            Game1.chatBox?.setText(msg);
            Game1.chatBox?.chatBox.RecieveCommandInput('\r');
        }
        _chatHud?.AddMessage("Nagi", msg);

        // farmhand 进程 → 推送 host 进程 /chat，确保小恒窗口可见（后台线程，不阻塞主线程）。
        if (Game1.player != null && !Game1.player.IsMainPlayer)
        {
            System.Threading.Tasks.Task.Run(() =>
            {
                try
                {
                    var json = System.Text.Json.JsonSerializer.Serialize(new { message = msg, color = "hotpink" });
                    using var client = new System.Net.Http.HttpClient();
                    client.PostAsync("http://localhost:7842/chat",
                        new System.Net.Http.StringContent(json, System.Text.Encoding.UTF8, "application/json")).Wait();
                }
                catch
                {
                    // 广播推送到 host 失败不阻塞游戏流程
                }
            });
        }
    }

    /// <summary>
    /// POST /cancel_sleep
    /// 取消睡觉：起身 + 关闭 ReadyCheckDialog。像真人一样"在别人上床前取消睡觉"。
    /// </summary>
    private object HandleCancelSleep()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                Game1.player.isInBed.Value = false;

                // 关闭 ReadyCheckDialog（睡觉就绪确认框）→ 起身
                if (Game1.activeClickableMenu is StardewValley.Menus.ReadyCheckDialog rcd)
                    rcd.exitThisMenu();
                Game1.activeClickableMenu = null;

                // 兜底：可能还残留其它菜单，直接清掉
                Game1.dialogueUp = false;

                tcs.SetResult(new { ok = true, action = "cancelled", wokeUp = true });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// POST /settlement_confirm
    /// AI 亲自关掉过夜结算界面（ShippingMenu）——过夜后收益结算时游戏时间暂停，
    /// 是 AI 和恒复盘今天/商量明天、聊完才进下一天的窗口；确认后新一天正式开跑。
    /// 只对当前进程生效（AI 进程 7843 的操作关的是 AI 自己的结算界面）。
    /// 超过 10 分钟没确认会兜底自动关（见 UpdateTicked 的 SettlementAutoDismissMs）。
    /// </summary>
    private object HandleSettlementConfirm()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                if (Game1.activeClickableMenu is StardewValley.Menus.ShippingMenu sm)
                {
                    sm.receiveLeftClick(sm.okButton.bounds.X, sm.okButton.bounds.Y);
                    tcs.SetResult(new { ok = true, action = "settlement_confirmed" });
                }
                else
                {
                    tcs.SetResult(new { ok = false, error = "当前不在过夜结算界面" });
                }
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// GET /ready_state
    /// 调试：反射读 Game1.netReadyChecks，确认睡觉 ready check 的真实 name（"sleep" 是否正确）。
    /// </summary>
    private object HandleReadyState()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var flags = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic
                    | System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Static;

                // FarmerTeam 里含 Ready 的方法
                var teamMethods = typeof(FarmerTeam).GetMethods(flags)
                    .Where(m => m.Name.Contains("Ready", StringComparison.OrdinalIgnoreCase))
                    .Select(m => m.Name).Distinct().ToList();

                // Game1 / FarmerTeam 里含 Ready 的字段/属性
                var game1Members = typeof(Game1).GetMembers(flags)
                    .Where(m => m.Name.Contains("Ready", StringComparison.OrdinalIgnoreCase))
                    .Select(m => $"{m.MemberType}:{m.Name}").Distinct().ToList();

                var teamMembers = typeof(FarmerTeam).GetMembers(flags)
                    .Where(m => m.Name.Contains("Ready", StringComparison.OrdinalIgnoreCase))
                    .Select(m => $"{m.MemberType}:{m.Name}").Distinct().ToList();

                // Game1.netReady 是 ReadySynchronizer，列出其方法 + 当前值
                var netReadyField = typeof(Game1).GetField("netReady", flags);
                var netReadyType = netReadyField?.FieldType?.Name ?? "(none)";
                var readySyncMethods = netReadyField?.FieldType?.GetMethods(flags)
                    .Select(m => m.Name).Distinct().ToList() ?? new List<string>();
                var netReadyVal = "";
                try
                {
                    var val = netReadyField?.GetValue(null);
                    netReadyVal = val?.ToString() ?? "null";
                }
                catch (Exception ex2) { netReadyVal = $"err:{ex2.Message}"; }

                tcs.SetResult(new
                {
                    ok = true,
                    netReadyType,
                    netReadyValue = netReadyVal,
                    readySynchronizerMethods = readySyncMethods,
                    farmerTeamReadyMethods = teamMethods,
                    game1ReadyMembers = game1Members,
                    farmerTeamReadyMembers = teamMembers
                });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// GET /screenshot
    /// 截取游戏当前画面，返回 base64 PNG（供 MCP screenshot 工具给 AI 看图）。
    /// </summary>
    private object HandleScreenshot()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var device = Game1.graphics.GraphicsDevice;
                int w = device.PresentationParameters.BackBufferWidth;
                int h = device.PresentationParameters.BackBufferHeight;
                var data = new Microsoft.Xna.Framework.Color[w * h];
                device.GetBackBufferData(data);
                using var tex = new Microsoft.Xna.Framework.Graphics.Texture2D(device, w, h);
                tex.SetData(data);
                using var ms = new MemoryStream();
                tex.SaveAsPng(ms, w, h);
                var base64 = Convert.ToBase64String(ms.ToArray());
                tcs.SetResult(new { ok = true, image = base64, width = w, height = h });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// GET /zoom?level=50
    /// 设置游戏缩放 zoomLevel（1-200%，如 50=50%）。不传 level 则只读当前值。
    /// 改的是 Game1.options.zoomLevel（随存档保存），下一帧生效，截图视野随之变化。
    /// </summary>
    private object HandleZoom(HttpListenerContext ctx)
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var qs = ctx.Request.QueryString;
        var levelStr = qs["level"];

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                float cur = Game1.options.zoomLevel;
                if (levelStr != null)
                {
                    int level = int.Parse(levelStr);
                    if (level < 1 || level > 200)
                    {
                        tcs.SetResult(new { ok = false, error = "level must be 1-200 (percent)", zoomLevel = cur });
                        return;
                    }
                    // SDV 1.6: zoomLevel 是只读派生属性。desiredBaseZoomLevel 是临时目标（换地图会被重置），
                    // 持久设置在这些字段里——全设一遍，确保换到任何地点都不回退到默认。
                    float z = level / 100f;
                    var opt = Game1.options;
                    var flags = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                    foreach (var fn in new[] { "baseZoomLevel", "singlePlayerBaseZoomLevel", "localCoopBaseZoomLevel" })
                        typeof(Options).GetField(fn, flags)?.SetValue(opt, z);
                    opt.desiredBaseZoomLevel = z;
                    cur = Game1.options.zoomLevel;
                }
                tcs.SetResult(new { ok = true, zoomLevel = cur, desiredBaseZoomLevel = Game1.options.desiredBaseZoomLevel, percent = (int)Math.Round(cur * 100) });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// GET /resolution?w=2560&h=1440
    /// 设置游戏窗口/back buffer 分辨率 → 截图也跟着高清（要 ≥2000 宽才能看清 50% 缩放的场景）。
    /// </summary>
    private object HandleResolution(HttpListenerContext ctx)
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var qs = ctx.Request.QueryString;
        int w = int.Parse(qs["w"] ?? "0");
        int h = int.Parse(qs["h"] ?? "0");
        if (w < 640 || h < 360)
            throw new InvalidOperationException("w/h must be >= 640x360");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                Game1.graphics.PreferredBackBufferWidth = w;
                Game1.graphics.PreferredBackBufferHeight = h;
                Game1.graphics.ApplyChanges();
                tcs.SetResult(new { ok = true, width = w, height = h, applied = true });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// POST /stop
    /// Cancels current movement.
    /// </summary>
    /// <summary>
    /// POST /queue  [{"action":"move","x":60,"y":17},{"action":"select","name":"Hoe"},{"action":"face","direction":2},{"action":"use"},...]
    /// Executes a sequence of commands automatically. Supported actions: move, face, select, use, interact, wait.
    /// Returns all results when the queue finishes.
    /// <summary>
    /// POST /key  { "key": "confirm" }
    /// Simulates a key press. Used to advance dialogue, confirm menus, skip cutscenes.
    /// Supported keys: confirm (action button), cancel (back/menu), skip (escape)
    /// </summary>
    /// <summary>
    /// POST /warp  { "location": "Beach", "x": 20, "y": 4 }
    /// Teleports the farmer to any game location. If x/y omitted, warps to default entry point.
    /// Common locations: Farm, Town, Beach, Mountain, Forest, Mine, BusStop, Desert, FishShop
    /// </summary>
    private object HandleWarp(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var location = GetParam<string>(p, "location");
        var x = GetParamOr(p, "x", -1);
        var y = GetParamOr(p, "y", -1);

        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var shopLocations = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
            { "SeedShop", "FishShop", "Blacksmith", "ScienceHouse", "AnimalShop", "Saloon", "AdventureGuild", "Hospital", "HatShop", "DesertTrade", "QiGemShop" };
        if (shopLocations.Contains(location) && Game1.player.freeSpotsInInventory() == 0)
            return new { ok = false, error = "Inventory full! Clear backpack before going to a shop.", freeSlots = 0 };

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var targetLoc = Game1.getLocationFromName(location);
                if (targetLoc == null)
                {
                    tcs.SetResult(new { ok = false, error = $"Location '{location}' not found" });
                    return;
                }

                ClearMovementState();

                // If no coordinates given, try to find a reasonable entry point
                if (x < 0 || y < 0)
                {
                    // Use the first warp that targets this location from current map, or default center
                    var farmer = Game1.player;
                    var curWarps = farmer.currentLocation.warps;
                    var matchWarp = curWarps.FirstOrDefault(w => w.TargetName == location);
                    if (matchWarp != null)
                    {
                        Game1.warpFarmer(location, matchWarp.TargetX, matchWarp.TargetY, false);
                    }
                    else
                    {
                        // Default: warp to center-ish of map
                        var mw = targetLoc.Map.DisplayWidth / 64;
                        var mh = targetLoc.Map.DisplayHeight / 64;
                        Game1.warpFarmer(location, mw / 2, mh / 2, false);
                    }
                }
                else
                {
                    var farmer = Game1.player;
                    if (farmer.currentLocation.Name == location)
                    {
                        farmer.Position = new Vector2(x, y) * Game1.tileSize;
                        CenterViewportOnFarmer(farmer);
                    }
                    else
                    {
                        Game1.warpFarmer(location, x, y, false);
                    }
                }

                var f = Game1.player;
                tcs.SetResult(new
                {
                    ok = true,
                    action = "warped",
                    requested = new { location, x, y },
                    actual = new { location = f.currentLocation.Name, x = f.TilePoint.X, y = f.TilePoint.Y }
                });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// POST /warp_into  { location, x?, y? }
    /// 同步切进任意地点（含建筑内部）。建筑内部 getLocationFromName 找不到、
    /// Game1.warpFarmer 也进不去，只能用 farm.buildings.indoors 引用直切。
    /// 直设 currentLocation + Position，同步生效（不等异步 tick），机器收放用。
    /// </summary>
    private object HandleWarpInto(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var location = GetParam<string>(p, "location");
        var x = GetParamOr(p, "x", -1);
        var y = GetParamOr(p, "y", -1);
        var near = GetParamOr(p, "near", false);   // true=绝不落目标格本身，只落四邻（装机器用）

        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var targetLoc = ResolveAnyLocation(location);
                if (targetLoc == null)
                {
                    tcs.SetResult(new { ok = false, error = $"找不到地点: {location}" });
                    return;
                }
                if (x < 0 || y < 0)
                {
                    // 默认落到该地点的出口 warp 处（门口/入口，天然可站，最自然的落点）
                    var entry = targetLoc.warps.FirstOrDefault();
                    if (entry != null)
                    {
                        x = entry.X;
                        y = entry.Y;
                    }
                    else
                    {
                        x = targetLoc.Map.DisplayWidth / 64 / 2;
                        y = targetLoc.Map.DisplayHeight / 64 / 2;
                    }
                }
                // 防 warp 进墙：只落到可站格，周围没有就报错（调用方跳过该机器）
                var walkable = FindWalkableTile(targetLoc, x, y, near);
                if (walkable == null)
                {
                    tcs.SetResult(new { ok = false, error = $"({x},{y}) 附近没有可站的格子" });
                    return;
                }
                WarpDirect(targetLoc, walkable.Value.x, walkable.Value.y);
                tcs.SetResult(new { ok = true, location = targetLoc.Name, x = walkable.Value.x, y = walkable.Value.y });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>解析地点：先按名（Game1.locations），再按建筑内部（farm.buildings.indoors）。</summary>
    private static GameLocation? ResolveAnyLocation(string location)
    {
        var byName = Game1.getLocationFromName(location);
        if (byName != null) return byName;
        var farm = Game1.getFarm();
        if (farm != null)
        {
            foreach (var b in farm.buildings)
            {
                var il = b.indoors?.Value;
                if (il != null && il.Name.Equals(location, StringComparison.OrdinalIgnoreCase))
                    return il;
            }
        }
        return null;
    }

    /// <summary>同步 warp：直接设 currentLocation + 位置（建筑内部只能引用直切）。</summary>
    private static void WarpDirect(GameLocation targetLoc, int x, int y)
    {
        var farmer = Game1.player;
        farmer.currentLocation = targetLoc;
        farmer.Position = new Vector2(x, y) * Game1.tileSize;
    }

    /// <summary>
    /// 在给定瓦片附近找一个可站格。防 warp 进墙/卡墙。
    /// skipCenter=true 时绝不落在目标格本身（只落四邻）——装机器用，避免站到可踩踏的机器（Cask）上。
    /// </summary>
    private static (int x, int y)? FindWalkableTile(GameLocation loc, int x, int y, bool skipCenter = false)
    {
        if (!skipCenter && IsWalkableTile(loc, x, y)) return (x, y);
        foreach (var (cx, cy) in new[] { (x, y - 1), (x, y + 1), (x - 1, y), (x + 1, y) })
        {
            if (IsWalkableTile(loc, cx, cy)) return (cx, cy);
        }
        return null;
    }

    /// <summary>
    /// 瓦片是否可站立：地图内 + isTilePassable + 物体可踏性。
    /// isTilePassable 只查地图碰撞，不查 loc.objects 里放的机器——
    /// 必须自己补：格上有不可踩踏物体（Keg 挡路）则不可站；可踩踏的（Cask）允许。
    /// </summary>
    private static bool IsWalkableTile(GameLocation loc, int x, int y)
    {
        if (loc?.Map?.Layers == null || loc.Map.Layers.Count == 0) return false;
        if (x < 0 || y < 0 || x >= loc.Map.Layers[0].LayerWidth || y >= loc.Map.Layers[0].LayerHeight)
            return false;
        var v = new Vector2(x, y);
        if (!loc.isTilePassable(v)) return false;
        if (loc.objects.TryGetValue(v, out var obj) && !obj.isPassable())
            return false;
        return true;
    }

    /// <summary>
    /// POST /warp_building  { bx, by, x?, y? }
    /// 按建筑在农场的 tileX/tileY 精确定位并切进其室内（多栋同名建筑如 Cabin 专用）。
    /// bx/by=建筑坐标，x/y=室内坐标（不给则室内中央）。
    /// </summary>
    private object HandleWarpBuilding(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var bx = GetParam<int>(p, "bx");
        var by = GetParam<int>(p, "by");
        var x = GetParamOr(p, "x", -1);
        var y = GetParamOr(p, "y", -1);
        var near = GetParamOr(p, "near", false);   // true=绝不落目标格本身，只落四邻（装机器用）

        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var farm = Game1.getFarm();
                var b = farm?.buildings.FirstOrDefault(bb => bb.tileX.Value == bx && bb.tileY.Value == by);
                if (b?.indoors?.Value is not GameLocation indoor)
                {
                    tcs.SetResult(new { ok = false, error = $"找不到建筑 @ Farm({bx},{by})" });
                    return;
                }
                if (x < 0 || y < 0)
                {
                    // 默认落到室内门口（室内第一个出口 warp 处，可站）
                    var door = indoor.warps.FirstOrDefault();
                    if (door != null)
                    {
                        x = door.X;
                        y = door.Y;
                    }
                    else
                    {
                        x = indoor.Map.DisplayWidth / 64 / 2;
                        y = indoor.Map.DisplayHeight / 64 / 2;
                    }
                }
                // 防 warp 进墙：只落到可站格
                var walkable = FindWalkableTile(indoor, x, y, near);
                if (walkable == null)
                {
                    tcs.SetResult(new { ok = false, error = $"室内 ({x},{y}) 附近没有可站的格子" });
                    return;
                }
                WarpDirect(indoor, walkable.Value.x, walkable.Value.y);
                tcs.SetResult(new { ok = true, location = indoor.Name, x = walkable.Value.x, y = walkable.Value.y, building = new { x = bx, y = by } });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// GET /focus — 把游戏窗口切到前台（备用；双窗口时别抢焦点，用 /set_pause）。
    /// </summary>
    private object HandleFocus()
    {
        try { SetForegroundWindow(Game1.game1.Window.Handle); return new { ok = true }; }
        catch (Exception ex) { return new { ok = false, error = ex.Message }; }
    }

    /// <summary>
    /// POST /set_pause { outOfFocus: bool }
    /// 设置"失焦暂停"选项。AI 自动化进程（7843）把它设为 false →
    /// 该窗口后台也能走位/拾取，不需要抢前台焦点（恒的 7842 窗口不受影响）。
    /// 脚本开头调 set_pause(false)，结束调 set_pause(true) 还原。
    /// </summary>
    private object HandleSetPause(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var outOfFocus = GetParamOr(p, "outOfFocus", false);

        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                bool was = Game1.options.pauseWhenOutOfFocus;
                Game1.options.pauseWhenOutOfFocus = outOfFocus;
                tcs.SetResult(new { ok = true, was, now = outOfFocus });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// POST /position { "x": 10, "y": 15 }
    /// Sets the farmer position on the current map and centers the camera.
    /// </summary>
    private object HandlePosition(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var x = GetParam<int>(p, "x");
        var y = GetParam<int>(p, "y");

        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            ClearMovementState();
            var farmer = Game1.player;
            farmer.Position = new Vector2(x, y) * Game1.tileSize;
            CenterViewportOnFarmer(farmer);
            tcs.SetResult(new
            {
                ok = true,
                action = "positioned",
                location = farmer.currentLocation.Name,
                x = farmer.TilePoint.X,
                y = farmer.TilePoint.Y
            });
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    private object HandleKey(HttpListenerContext ctx)
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var p = ReadJson(ctx);
        var key = GetParamOr(p, "key", "confirm");
        var count = GetParamOr(p, "count", 1);
        var hold = GetParamOr(p, "hold", 0);  // 长按毫秒（模拟按住走到边缘/传送瓦片）
        var noFocus = GetParamOr(p, "nofocus", false);  // true=不抢前台（后台操作菜单/按键时别顶掉恒的窗口）

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                // 发键前把游戏窗口拉到前台（否则终端占焦点时按键发不到游戏）。
                // ⚠️ 双开实例时抢焦点会顶掉恒的窗口——后台操作传 nofocus=true 跳过。
                if (!noFocus)
                {
                    try { SetForegroundWindow(Game1.game1.Window.Handle); } catch { }
                }
                for (int i = 0; i < count; i++)
                {
                    switch (key.ToLower())
                    {
                        case "confirm":
                        case "action":
                            if (Game1.currentMinigame != null)
                            {
                                Game1.currentMinigame.receiveKeyPress(Keys.Enter);
                                keybd_event(0x0D, 0, 0, UIntPtr.Zero);
                                System.Threading.Thread.Sleep(50);
                                keybd_event(0x0D, 0, KEYEVENTF_KEYUP, UIntPtr.Zero);
                            }
                            else if (Game1.activeClickableMenu is DialogueBox dialogueBox)
                            {
                                // 🆕 2026-08-18 恒：receiveKeyPress(Enter) 对【无选项】对话不推进（AI 经常"点不掉"，
                                //    如沙漠节厨师「那实在太好了」）——有选项才 Enter 选，无选项用 receiveLeftClick 点对话框中心推进
                                //    （=/click 的推进方式，实测有效；pressActionButton 对无选项 DialogueBox 无效）。
                                bool hasResp = false;
                                try
                                {
                                    var rF = typeof(DialogueBox).GetField("responses",
                                        System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
                                    var rA = rF?.GetValue(dialogueBox) as IEnumerable<Response>;
                                    hasResp = rA != null && rA.Any();
                                }
                                catch { }
                                if (hasResp)
                                    dialogueBox.receiveKeyPress(Keys.Enter);
                                else
                                    dialogueBox.receiveLeftClick(
                                        dialogueBox.xPositionOnScreen + dialogueBox.width / 2,
                                        dialogueBox.yPositionOnScreen + dialogueBox.height / 2);
                            }
                            else if (Game1.activeClickableMenu != null)
                            {
                                Game1.activeClickableMenu.receiveLeftClick(
                                    Game1.activeClickableMenu.xPositionOnScreen + Game1.activeClickableMenu.width / 2,
                                    Game1.activeClickableMenu.yPositionOnScreen + Game1.activeClickableMenu.height / 2);
                            }
                            else if (Game1.currentLocation?.currentEvent != null)
                            {
                                Game1.currentLocation.currentEvent.receiveActionPress(0, 0);
                            }
                            else if (Game1.input != null)
                            {
                                Game1.pressActionButton(Game1.input.GetKeyboardState(), Game1.input.GetMouseState(),
                                    Game1.input.GetGamePadState());
                            }
                            break;
                        case "ok":
                            if (Game1.activeClickableMenu != null)
                            {
                                var okBtn = Game1.activeClickableMenu.GetType()
                                    .GetField("okButton", System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Instance)?
                                    .GetValue(Game1.activeClickableMenu) as ClickableTextureComponent;
                                if (okBtn != null)
                                {
                                    Game1.activeClickableMenu.receiveLeftClick(
                                        okBtn.bounds.Center.X, okBtn.bounds.Center.Y);
                                }
                                else
                                {
                                    Game1.activeClickableMenu.exitThisMenu();
                                }
                            }
                            break;
                        case "menu":
                            if (Game1.activeClickableMenu != null)
                                Game1.activeClickableMenu.receiveKeyPress(Keys.Escape);
                            else
                                Game1.activeClickableMenu = new GameMenu();
                            break;
                        case "cancel":
                        case "back":
                            if (Game1.activeClickableMenu != null)
                                Game1.activeClickableMenu.receiveKeyPress(Keys.Escape);
                            else if (Game1.input != null)
                                Game1.pressUseToolButton();
                            break;
                        case "down":
                            // 梯子触发：用 pressActionButton（游戏标准交互）
                            try
                            {
                                if (Game1.input != null)
                                {
                                    Game1.pressActionButton(Game1.input.GetKeyboardState(),
                                        Game1.input.GetMouseState(), Game1.input.GetGamePadState());
                                }
                            }
                            catch { }
                            // 等游戏更新循环处理
                            System.Threading.Thread.Sleep(300);
                            // 补发 Win32 按键，持续时间长一点
                            keybd_event(0x28, 0, 0, UIntPtr.Zero);
                            System.Threading.Thread.Sleep(200);
                            keybd_event(0x28, 0, KEYEVENTF_KEYUP, UIntPtr.Zero);
                            break;
                        case "skip":
                        case "escape":
                            if (Game1.currentLocation?.currentEvent != null)
                            {
                                Game1.currentLocation.currentEvent.skipped = true;
                                Game1.currentLocation.currentEvent.skipEvent();
                            }
                            else
                            {
                                Game1.currentMinigame?.receiveKeyPress(Keys.Escape);
                                if (Game1.activeClickableMenu != null)
                                    Game1.activeClickableMenu.receiveKeyPress(Keys.Escape);
                            }
                            break;
                        default:
                            byte? virtualKey = null;
                            if (key.ToLower().StartsWith("f") && int.TryParse(key.Substring(1), out int fNum) && fNum >= 1 && fNum <= 12)
                                virtualKey = (byte)(0x70 + fNum - 1);
                            else if (key.Equals("space", StringComparison.OrdinalIgnoreCase)) virtualKey = 0x20;
                            else if (key.Equals("enter", StringComparison.OrdinalIgnoreCase)) virtualKey = 0x0D;
                            else if (key.Equals("up", StringComparison.OrdinalIgnoreCase)) virtualKey = 0x26;
                            else if (key.Equals("down", StringComparison.OrdinalIgnoreCase)) virtualKey = 0x28;
                            else if (key.Equals("left", StringComparison.OrdinalIgnoreCase)) virtualKey = 0x25;
                            else if (key.Equals("right", StringComparison.OrdinalIgnoreCase)) virtualKey = 0x27;
                            else if (key.Equals("w", StringComparison.OrdinalIgnoreCase)) virtualKey = 0x57;
                            else if (key.Equals("a", StringComparison.OrdinalIgnoreCase)) virtualKey = 0x41;
                            else if (key.Equals("s", StringComparison.OrdinalIgnoreCase)) virtualKey = 0x53;
                            else if (key.Equals("d", StringComparison.OrdinalIgnoreCase)) virtualKey = 0x44;
                            // 数字键 0-9（用于对话框选项选择）
                            else if (key.Length == 1 && key[0] >= '0' && key[0] <= '9')
                            {
                                virtualKey = (byte)(0x30 + (key[0] - '0'));
                                // 同时通知活动菜单（DialogueBox 用 receiveKeyPress 处理数字选选项）
                                try { Game1.activeClickableMenu?.receiveKeyPress((Keys)(virtualKey.Value)); } catch { }
                            }
                            if (virtualKey.HasValue)
                            {
                                if (Game1.currentMinigame != null && Enum.TryParse<Keys>(key, true, out var xnaKey))
                                    Game1.currentMinigame.receiveKeyPress(xnaKey);
                                keybd_event(virtualKey.Value, 0, 0, UIntPtr.Zero);
                                // 移动键默认按住 500ms（走到边缘/传送瓦片触发）；其他键 50ms 轻点
                                bool isMoveKey = key.Equals("w", StringComparison.OrdinalIgnoreCase)
                                    || key.Equals("a", StringComparison.OrdinalIgnoreCase)
                                    || key.Equals("s", StringComparison.OrdinalIgnoreCase)
                                    || key.Equals("d", StringComparison.OrdinalIgnoreCase)
                                    || key.Equals("up", StringComparison.OrdinalIgnoreCase)
                                    || key.Equals("down", StringComparison.OrdinalIgnoreCase)
                                    || key.Equals("left", StringComparison.OrdinalIgnoreCase)
                                    || key.Equals("right", StringComparison.OrdinalIgnoreCase);
                                int holdMs = hold > 0 ? hold : (isMoveKey ? 500 : 50);
                                System.Threading.Thread.Sleep(holdMs);
                                keybd_event(virtualKey.Value, 0, KEYEVENTF_KEYUP, UIntPtr.Zero);
                            }
                            break;
                    }
                }
                tcs.SetResult(new { ok = true, key, count });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }


    /// <summary>
    /// POST /minigame_click  { x?, y?, action? }
    /// 🎰 赌场小游戏（老虎机 Slots / 21点 CalicoJack）点按钮——游戏原生 Minigame（存 Game1.currentMinigame），
    ///    **不是 activeClickableMenu**，所以 /menu/click 对它无效。这里直接调 currentMinigame.receiveLeftClick。
    /// action 用语义点名（bet10/bet100/hit/stand/double/play_again/done/quit）→ 反射读该按钮字段的 bounds.Center
    ///    （坐标依赖 viewport/zoomLevel/localMultiplayerWindow，运行时才准，故反射读构造后 bounds 最稳，不靠自己拼公式）。
    /// 没传 action 则用裸坐标 x,y（落按钮 bounds 内由游戏判 Contains）。
    /// 2026-08-23 恒：赌场小游戏进了不能中途关，只能打完一局或用 quit(21点)/done(老虎机)退出。
    /// </summary>
    private object HandleMinigameClick(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var x = GetParamOr(p, "x", -1);
        var y = GetParamOr(p, "y", -1);
        var action = GetParamOr(p, "action", "");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var mg = Game1.currentMinigame;
                if (mg == null)
                {
                    tcs.SetResult(new { ok = false, error = "No minigame active" });
                    return;
                }
                // 语义点名（推荐，AI 用）：反射定位按钮 bounds 中心，无需 Python 算缩放坐标
                if (!string.IsNullOrEmpty(action) && TryClickMinigameButton(mg, action, out var cx, out var cy))
                {
                    mg.receiveLeftClick(cx, cy);
                    tcs.SetResult(new { ok = true, clicked = "minigame_button", action, x = cx, y = cy, minigame = mg.GetType().Name });
                    return;
                }
                // 裸坐标（x,y 落在 bounds 内直接点，由游戏判 Contains）
                if (x >= 0 && y >= 0)
                {
                    mg.receiveLeftClick(x, y);
                    tcs.SetResult(new { ok = true, clicked = "minigame_pos", x, y, minigame = mg.GetType().Name });
                    return;
                }
                tcs.SetResult(new { ok = false, error = "need action or x/y" });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>反射读当前小游戏某个 private ClickableComponent 字段的按钮 bounds 中心。</summary>
    private static bool TryGetMinigameButtonCenter(object mg, string fieldName, out int cx, out int cy)
    {
        cx = cy = -1;
        var f = mg.GetType().GetField(fieldName,
            System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
        if (f?.GetValue(mg) is ClickableComponent cc)
        {
            cx = cc.bounds.Center.X;
            cy = cc.bounds.Center.Y;
            return true;
        }
        return false;
    }

    /// <summary>语义 action → 对应小游戏的按钮字段名（反编译 Slots.cs/CalicoJack.cs 确认，字段全 private）。</summary>
    private static bool TryClickMinigameButton(object mg, string action, out int cx, out int cy)
    {
        cx = cy = -1;
        // ⚠️ 按小游戏类型分派 action→字段，否则 quit/done/hit/stand 跨游戏冲突（如 "done" or "quit" 会劫持 quit）。
        //    老虎机 Slots：bet10=spinButton10 / bet100=spinButton100 / done=doneButton(也作 quit 退出)；
        //    21点 CalicoJack：hit=加牌 / stand=停牌 / double=加倍 / play_again=新游戏 / quit=退出(→currentMinigame=null)。
        string field = (mg, action) switch
        {
            (Slots, "bet10") => "spinButton10",
            (Slots, "bet100") => "spinButton100",
            (Slots, "done") or (Slots, "quit") => "doneButton",
            (CalicoJack, "hit") => "hit",
            (CalicoJack, "stand") => "stand",
            (CalicoJack, "double") => "doubleOrNothing",
            (CalicoJack, "play_again") => "playAgain",
            (CalicoJack, "quit") => "quit",
            _ => ""
        };
        return field != "" && TryGetMinigameButtonCenter(mg, field, out cx, out cy);
    }


    /// <summary>
    /// GET /minigame_state
    /// 🎰 读当前小游戏的状态摘要（牌面/组合/赌注/结果）——让 AI 不截图也能知道现状。
    /// CalicoJack(21点)：玩家/庄家点数、赌注、是否结果屏、是否赢；Slots(老虎机)：当前组合、赌注、转盘、余额。
    /// 字段多是 private（反编译确认）→ 反射读。cards[i] = int[2]{点数, 花色标记}（-1=盖牌,400=翻开,999=特殊）。
    /// </summary>
    private object HandleMinigameState()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");
        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var mg = Game1.currentMinigame;
                if (mg == null)
                {
                    tcs.SetResult(new { ok = true, minigame = (string?)null });
                    return;
                }
                var flags = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic
                          | System.Reflection.BindingFlags.Instance;
                object? GetField(object o, string name) => o.GetType().GetField(name, flags)?.GetValue(o);

                if (mg is CalicoJack cj)
                {
                    // playerCards/dealerCards 是 public List<int[]>（反编译确认）；currentBet/showingResultsScreen/playerWon 是 private
                    int CardValue(int[] c) => c != null && c.Length > 0 ? c[0] : -1;   // 点数
                    var pc = GetField(cj, "playerCards") as System.Collections.IEnumerable;
                    var dc = GetField(cj, "dealerCards") as System.Collections.IEnumerable;
                    var playerVals = new List<int>(); var dealerVals = new List<int>();
                    if (pc != null) foreach (var c in pc) if (c is int[] arr) playerVals.Add(CardValue(arr));
                    if (dc != null) foreach (var c in dc) if (c is int[] arr2) dealerVals.Add(CardValue(arr2));
                    tcs.SetResult(new
                    {
                        ok = true,
                        minigame = "CalicoJack",
                        playerCards = playerVals,
                        dealerCards = dealerVals,
                        playerTotal = playerVals.Where((v, i) => !(i == 0 && v == -1)).Sum(),   // 玩家牌全亮
                        dealerUp = dealerVals.Count > 0 ? dealerVals[0] : -2,                    // 庄家明牌(dealerCards[0][0])
                        currentBet = (int?)GetField(cj, "currentBet") ?? -1,
                        showingResultsScreen = (bool?)GetField(cj, "showingResultsScreen") ?? false,
                        playerWon = (bool?)GetField(cj, "playerWon") ?? false,
                        highStakes = (bool?)GetField(cj, "highStakes") ?? false
                    });
                    return;
                }
                if (mg is Slots s)
                {
                    var slots = GetField(s, "slots") as System.Collections.IEnumerable;   // List<float> 当前转盘位置
                    var vals = new List<float>();
                    if (slots != null) foreach (var v in slots) vals.Add(Convert.ToSingle(v));
                    tcs.SetResult(new
                    {
                        ok = true,
                        minigame = "Slots",
                        slots = vals,                                   // 3 个转盘当前值(0~7 图标索引)
                        currentBet = (int?)GetField(s, "currentBet") ?? -1,
                        spinning = (bool?)GetField(s, "spinning") ?? false,
                        showResult = (bool?)GetField(s, "showResult") ?? false,
                        payoutModifier = (float?)GetField(s, "payoutModifier") ?? 0f,
                        clubCoins = Game1.player.clubCoins
                    });
                    return;
                }
                tcs.SetResult(new { ok = true, minigame = mg.GetType().Name });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// </summary>
    private object HandleQueue(HttpListenerContext ctx)
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        using var reader = new StreamReader(ctx.Request.InputStream, ctx.Request.ContentEncoding);
        var body = reader.ReadToEnd();
        if (string.IsNullOrWhiteSpace(body))
            throw new InvalidOperationException("Empty command queue");

        var commands = JsonSerializer.Deserialize<List<Dictionary<string, object?>>>(body);
        if (commands == null || commands.Count == 0)
            throw new InvalidOperationException("No commands in queue");

        _commandQueueTcs = new TaskCompletionSource<object>();
        _commandResults.Clear();

        EnqueueMainThread(() =>
        {
            _commandQueue = new Queue<Dictionary<string, object?>>(commands);
            _commandDelay = 0;
            _waitingForMove = false;
        });

        // Wait for all commands to execute (timeout 5 minutes)
        if (_commandQueueTcs.Task.Wait(TimeSpan.FromMinutes(5)))
            return _commandQueueTcs.Task.Result;
        else
            return new { ok = false, error = "Queue execution timed out", executed = _commandResults.Count };
    }

    private object HandleStop()
    {
        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            ClearMovementState();
            var farmer = Game1.player;
            tcs.SetResult(new
            {
                ok = true,
                message = "Movement stopped",
                location = farmer.currentLocation.Name,
                x = farmer.TilePoint.X,
                y = farmer.TilePoint.Y
            });
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    private object HandlePlaceChest(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var cx = GetParam<int>(p, "x");
        var cy = GetParam<int>(p, "y");

        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            var loc = Game1.player.currentLocation;
            var tileVec = new Vector2(cx, cy);

            if (loc.objects.ContainsKey(tileVec))
            {
                tcs.SetResult(new { ok = false, error = $"Tile ({cx},{cy}) already has an object" });
                return;
            }

            var chest = new StardewValley.Objects.Chest(true, tileVec);
            loc.objects.Add(tileVec, chest);
            tcs.SetResult(new { ok = true, placed = "Chest", x = cx, y = cy });
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    private object HandleStore(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var cx = GetParam<int>(p, "x");
        var cy = GetParam<int>(p, "y");
        var name = GetParamOr(p, "name", "");
        var count = GetParamOr(p, "count", int.MaxValue);
        var keepTools = GetParamOr(p, "keepTools", true);

        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            var farmer = Game1.player;
            var loc = farmer.currentLocation;
            var tileVec = new Vector2(cx, cy);

            if (!loc.objects.TryGetValue(tileVec, out var obj) || obj is not StardewValley.Objects.Chest chest)
            {
                tcs.SetResult(new { ok = false, error = $"No chest at ({cx},{cy})" });
                return;
            }

            var stored = new List<object>();
            int remaining = count;
            for (int i = farmer.Items.Count - 1; i >= 0 && remaining > 0; i--)
            {
                var item = farmer.Items[i];
                if (item == null) continue;
                if (keepTools && item is Tool) continue;
                if (!string.IsNullOrEmpty(name)
                    && !item.Name.Equals(name, StringComparison.OrdinalIgnoreCase))
                    continue;

                // 如果指定了数量，只存需要的部分
                if (count != int.MaxValue && item.Stack > remaining)
                {
                    var toStore = item.getOne();
                    toStore.Stack = remaining;
                    var leftover = chest.addItem(toStore);
                    int storedCount = remaining - (leftover?.Stack ?? 0);
                    if (storedCount > 0)
                    {
                        item.Stack -= storedCount;
                        stored.Add(new { item = item.Name, count = storedCount });
                        remaining -= storedCount;
                    }
                }
                else
                {
                    var leftover = chest.addItem(item);
                    if (leftover == null)
                    {
                        int moved = item.Stack;
                        stored.Add(new { item = item.Name, count = moved });
                        farmer.Items[i] = null;
                        if (count != int.MaxValue) remaining -= moved;
                    }
                    else if (leftover.Stack < item.Stack)
                    {
                        int moved = item.Stack - leftover.Stack;
                        stored.Add(new { item = item.Name, count = moved });
                        farmer.Items[i] = leftover;
                        if (count != int.MaxValue) remaining -= moved;
                    }
                }
            }

            tcs.SetResult(new { ok = true, stored, chestAt = new { x = cx, y = cy } });
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// POST /store_all — 场景内智能存储（"堆高高"）+ 用户指定箱。
    /// Body: { keepTools?: bool=true, what?: string|string[], target?: {color?|name?|x,y}, default?: {x,y} }
    /// 只处理当前场景的箱子（恒拍板：不跨地图；范围：宝箱/大箱子/石箱，is Chest 全覆盖）。
    /// 跨箱路由决策（C# 原子完成，不移动玩家）：
    ///   智能模式：已有同类堆的箱子(取空位最多) → 默认箱 → 空位最多箱 → 全满记 leftover
    ///   target 模式：用户指定箱（颜色/名字/坐标），what(或全部非工具) 直放该箱
    /// </summary>
    private object HandleStoreAll(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var keepTools = GetParamOr(p, "keepTools", true);

        // what: 支持字符串（逗号/空格分隔）或字符串数组
        var whatList = new List<string>();
        if (p.TryGetValue("what", out var wv) && wv is JsonElement wJe)
        {
            if (wJe.ValueKind == JsonValueKind.Array)
                foreach (var el in wJe.EnumerateArray())
                    whatList.Add(el.GetString() ?? "");
            else if (wJe.ValueKind == JsonValueKind.String)
                foreach (var s in (wJe.GetString() ?? "").Split(new[] { ',', '，', ' ', '|' }, StringSplitOptions.RemoveEmptyEntries))
                    whatList.Add(s.Trim());
        }

        // target / default
        string? targetColor = null, targetName = null;
        int targetX = -1, targetY = -1, defX = -1, defY = -1;
        if (p.TryGetValue("target", out var tv) && tv is JsonElement tJe && tJe.ValueKind == JsonValueKind.Object)
        {
            if (tJe.TryGetProperty("color", out var ce)) targetColor = ce.GetString();
            if (tJe.TryGetProperty("name", out var ne)) targetName = ne.GetString();
            if (tJe.TryGetProperty("x", out var xe)) targetX = xe.GetInt32();
            if (tJe.TryGetProperty("y", out var ye)) targetY = ye.GetInt32();
        }
        if (p.TryGetValue("default", out var dv) && dv is JsonElement dJe && dJe.ValueKind == JsonValueKind.Object)
        {
            if (dJe.TryGetProperty("x", out var xe)) defX = xe.GetInt32();
            if (dJe.TryGetProperty("y", out var ye)) defY = ye.GetInt32();
        }

        bool targetMode = targetColor != null || targetName != null || (targetX >= 0 && targetY >= 0);

        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var farmer = Game1.player;
                var loc = farmer.currentLocation;
                if (loc == null) { tcs.SetResult(new { ok = false, error = "No location" }); return; }

                // 当前场景所有存储箱（宝箱/大箱子/石箱 + 内置冰箱；排除迷你出货箱/祝尼魔箱）
                var chests = CollectStorageChests(loc);
                var tileOf = new Dictionary<StardewValley.Objects.Chest, Vector2>();
                var labelOf = new Dictionary<StardewValley.Objects.Chest, string>();
                foreach (var (c, t, lb) in chests) { tileOf[c] = t; labelOf[c] = lb; }

                // 显示名：已标记用 Name，内置冰箱用 label，其余用 ChestName
                string DisplayName(StardewValley.Objects.Chest c) =>
                    DisplayChestName(c, labelOf.TryGetValue(c, out var lb) ? lb : "");

                if (chests.Count == 0)
                {
                    tcs.SetResult(new { ok = true, mode = targetMode ? "target" : "smart",
                        location = loc.Name, stored = new List<object>(), leftovers = new List<object>(),
                        chests = new List<object>(), totalFree = 0, note = "当前场景没有箱子" });
                    return;
                }

                int Free(StardewValley.Objects.Chest ch) => ch.GetActualCapacity() - ch.Items.Count(i => i != null);

                Chest? SpaceChest()
                {
                    Chest? b = null; int bf = -1;
                    foreach (var (c, _, _) in chests) { int f = Free(c); if (f > bf) { bf = f; b = c; } }
                    return bf > 0 ? b : null;
                }

                bool WhatMatches(Item it)
                {
                    if (whatList.Count == 0) return true;
                    var name = it.Name ?? "";
                    var dn = it.DisplayName ?? "";
                    var qid = it.QualifiedItemId ?? "";
                    return whatList.Any(w =>
                        string.Equals(name, w, StringComparison.OrdinalIgnoreCase)
                        || string.Equals(dn, w, StringComparison.OrdinalIgnoreCase)
                        || string.Equals(qid, w, StringComparison.OrdinalIgnoreCase));
                }

                // 指定箱匹配：坐标 > 颜色(最近) > 名字(子串)
                Chest? ResolveTarget()
                {
                    if (targetX >= 0 && targetY >= 0)
                    {
                        foreach (var (c, t, _) in chests)
                            if ((int)t.X == targetX && (int)t.Y == targetY) return c;
                        return null;
                    }
                    if (!string.IsNullOrEmpty(targetColor))
                    {
                        Chest? best = null; double bestDist = double.MaxValue;
                        var (tr, tg, tb) = ParseHex(targetColor);
                        foreach (var (c, _, _) in chests)
                        {
                            var ch = ChestColorHex(c);
                            if (ch.Length == 7 && int.TryParse(ch.Substring(1, 2), System.Globalization.NumberStyles.HexNumber, null, out var cr)
                                && int.TryParse(ch.Substring(3, 2), System.Globalization.NumberStyles.HexNumber, null, out var cg)
                                && int.TryParse(ch.Substring(5, 2), System.Globalization.NumberStyles.HexNumber, null, out var cb))
                            {
                                double d = (tr - cr) * (tr - cr) + (tg - cg) * (tg - cg) + (tb - cb) * (tb - cb);
                                if (d < bestDist) { bestDist = d; best = c; }
                            }
                        }
                        return best;
                    }
                    if (!string.IsNullOrEmpty(targetName))
                    {
                        foreach (var (c, _, _) in chests)
                            if (DisplayName(c).IndexOf(targetName, StringComparison.OrdinalIgnoreCase) >= 0) return c;
                        return null;
                    }
                    return null;
                }

                // 智能模式默认箱：请求指定坐标，否则自动选空位最多
                Chest? defChest = null;
                if (defX >= 0 && defY >= 0)
                    foreach (var (c, t, _) in chests)
                        if ((int)t.X == defX && (int)t.Y == defY) { defChest = c; break; }
                if (defChest == null)
                {
                    int bf = -1;
                    foreach (var (c, _, _) in chests) { int f = Free(c); if (f > bf) { bf = f; defChest = c; } }
                }

                Chest? targetChest = targetMode ? ResolveTarget() : null;

                var stored = new List<object>();
                var leftovers = new List<object>();
                for (int i = farmer.Items.Count - 1; i >= 0; i--)
                {
                    var item = farmer.Items[i];
                    if (item == null) continue;
                    if (keepTools && item is Tool) continue;
                    if (!WhatMatches(item)) continue;

                    Chest? target = null;
                    string reason = "";
                    if (targetMode)
                    {
                        if (targetChest == null) reason = "target_not_found";
                        else if (Free(targetChest) <= 0) reason = "target_full";
                        else target = targetChest;
                    }
                    else
                    {
                        // 1. 已有同类堆的箱子（空位最多）
                        int bf = -1;
                        foreach (var (c, _, _) in chests)
                        {
                            int f = Free(c);
                            if (f <= 0) continue;
                            bool hasSame = c.Items.Any(ci => ci != null && ci.QualifiedItemId == item.QualifiedItemId);
                            if (hasSame && f > bf) { bf = f; target = c; }
                        }
                        // 2. 默认箱
                        if (target == null && defChest != null && Free(defChest) > 0) target = defChest;
                        // 3. 空位最多箱
                        if (target == null) target = SpaceChest();
                        if (target == null) reason = "all_chests_full";
                    }

                    if (target == null)
                    {
                        leftovers.Add(new { item = item.Name, count = item.Stack, reason });
                        continue;
                    }

                    var leftover = target.addItem(item);
                    var to = tileOf[target];
                    if (leftover == null)
                    {
                        stored.Add(new { item = item.Name, count = item.Stack,
                            to = new { x = (int)to.X, y = (int)to.Y, name = DisplayName(target), color = ChestColorHex(target) } });
                        farmer.Items[i] = null;
                    }
                    else if (leftover.Stack < item.Stack)
                    {
                        int moved = item.Stack - leftover.Stack;
                        stored.Add(new { item = item.Name, count = moved,
                            to = new { x = (int)to.X, y = (int)to.Y, name = DisplayName(target), color = ChestColorHex(target) } });
                        farmer.Items[i] = leftover;
                    }
                    else
                    {
                        leftovers.Add(new { item = item.Name, count = item.Stack, reason = "chest_rejected" });
                    }
                }

                var chestSummary = chests.Select(x => (object)new
                {
                    location = loc.Name,
                    x = (int)tileOf[x.c].X,
                    y = (int)tileOf[x.c].Y,
                    name = DisplayName(x.c),
                    color = ChestColorHex(x.c),
                    used = x.c.Items.Count(i => i != null),
                    capacity = x.c.GetActualCapacity(),
                    freeSlots = Free(x.c)
                }).ToList();

                tcs.SetResult(new
                {
                    ok = true,
                    mode = targetMode ? "target" : "smart",
                    location = loc.Name,
                    stored,
                    leftovers,
                    chests = chestSummary,
                    totalFree = chests.Sum(x => Free(x.c))
                });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// POST /name_chest  { "x": 20, "y": 15, "tag": "矿石" } — 给箱子加括号标记
    /// 名字 = 本名(标记)，如 宝箱(矿石) / 迷你冰箱(食物)。tag 留空 = 清除标记只留本名。
    /// 本名 = 现名去掉尾部 (xxx)；默认物品名(没自定义过) 用本地化显示名。直写 chest.Name（SDV 1.6 会存）。
    /// </summary>
    private object HandleNameChest(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var cx = GetParam<int>(p, "x");
        var cy = GetParam<int>(p, "y");
        var tag = GetParamOr(p, "tag", "");

        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var loc = Game1.player.currentLocation;
                if (loc == null) { tcs.SetResult(new { ok = false, error = "No location" }); return; }

                Chest? chest = null;
                string label = "";
                foreach (var (c, t, lb) in CollectStorageChests(loc))
                    if ((int)t.X == cx && (int)t.Y == cy) { chest = c; label = lb; break; }
                if (chest == null)
                {
                    tcs.SetResult(new { ok = false, error = $"No storage chest at ({cx},{cy})" });
                    return;
                }

                // 本名：内置冰箱用合成 label（"内置冰箱"），其余用 ChestBaseName（默认物品名→本地化名）
                var baseName = label != "" ? label : ChestBaseName(chest);
                var newName = string.IsNullOrWhiteSpace(tag) ? baseName : $"{baseName}({tag.Trim()})";
                chest.Name = newName;
                tcs.SetResult(new { ok = true, x = cx, y = cy, tag = tag.Trim(), name = newName });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// POST /drop  { "name": "Wood", "count": 5 }
    /// 从背包丢弃指定数量物品（直接消失，不落地面）。count 默认 1。
    /// </summary>
    private object HandleDrop(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var name = GetParam<string>(p, "name");
        var count = GetParamOr(p, "count", 1);

        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var farmer = Game1.player;
                int removed = 0;
                int remaining = count;
                for (int i = farmer.Items.Count - 1; i >= 0 && remaining > 0; i--)
                {
                    var item = farmer.Items[i];
                    if (item == null) continue;
                    if (!item.Name.Equals(name, StringComparison.OrdinalIgnoreCase)
                        && !(item.QualifiedItemId ?? "").Equals(name, StringComparison.OrdinalIgnoreCase))
                        continue;

                    int toRemove = Math.Min(remaining, item.Stack);
                    item.Stack -= toRemove;
                    removed += toRemove;
                    remaining -= toRemove;
                    if (item.Stack <= 0)
                        farmer.Items[i] = null;
                }

                int inventoryLeft = farmer.Items.Count(i => i != null);
                tcs.SetResult(new { ok = true, name, removed, inventoryLeft });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// POST /gift  { "target": "Leah" | "小恒", "item": "Grape" }
    /// 送礼。target 是当前地图 NPC → 走真实好感度系统（tryToReceiveActiveObject，含每周2次/每日1次限制）；
    /// target 是玩家 → 物品转移（SDV 玩家间无好感度条，只传物品）。
    /// </summary>
    private object HandleGift(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var target = GetParam<string>(p, "target");
        var itemName = GetParam<string>(p, "item");

        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var farmer = Game1.player;
                var loc = farmer.currentLocation;

                // 找背包物品（先匹配名字，再匹配 QualifiedItemId）
                int idx = -1;
                Item item = null;
                for (int i = 0; i < farmer.Items.Count; i++)
                {
                    var it = farmer.Items[i];
                    if (it == null) continue;
                    if (it.Name.Equals(itemName, StringComparison.OrdinalIgnoreCase)
                        || (it.QualifiedItemId ?? "").Equals(itemName, StringComparison.OrdinalIgnoreCase))
                    { idx = i; item = it; break; }
                }
                if (item == null)
                {
                    tcs.SetResult(new { ok = false, error = $"背包里没有 {itemName}" });
                    return;
                }

                // ── 1) 目标是 NPC（当前地图）→ 真实好感度送礼 ──
                var npc = loc.characters.FirstOrDefault(c => c is NPC n && n.Name == target) as NPC;
                if (npc != null)
                {
                    if (!npc.CanReceiveGifts())
                    {
                        tcs.SetResult(new { ok = false, error = $"{npc.Name} 不能收礼（不可社交或本周已送满）" });
                        return;
                    }
                    if (item is not StardewValley.Object giftObj || !item.canBeGivenAsGift())
                    {
                        tcs.SetResult(new { ok = false, error = $"{item.Name} 不能作为礼物" });
                        return;
                    }

                    // 确保好感度记录存在（receiveGift 内部访问 friendshipData[npc].GiftsToday）
                    if (!farmer.friendshipData.ContainsKey(npc.Name))
                        farmer.friendshipData[npc.Name] = new StardewValley.Friendship(0);

                    int before = farmer.friendshipData.TryGetValue(npc.Name, out var f0) ? f0.Points : 0;

                    // 从背包取 1 个，设为手持（ActiveObject），走真实送礼流程
                    var giftOne = (StardewValley.Object)item.getOne();
                    item.Stack -= 1;
                    if (item.Stack <= 0) farmer.Items[idx] = null;
                    var prevActive = farmer.ActiveObject;
                    farmer.ActiveObject = giftOne;

                    bool success;
                    try
                    {
                        success = npc.tryToReceiveActiveObject(farmer);
                    }
                    catch (Exception)
                    {
                        success = false;
                    }
                    if (!success)
                    {
                        // 送礼被拒（本周满/今日已送/其他）→ 退回物品
                        farmer.ActiveObject = prevActive;
                        farmer.addItemToInventory(giftOne);
                        tcs.SetResult(new { ok = false, error = $"{npc.Name} 拒收了礼物（可能本周已送满或今天已送过）" });
                        return;
                    }

                    int after = farmer.friendshipData.TryGetValue(npc.Name, out var f1) ? f1.Points : 0;
                    int taste = npc.getGiftTasteForThisItem(giftOne);
                    tcs.SetResult(new
                    {
                        ok = true,
                        action = "gifted_npc",
                        target = npc.Name,
                        item = giftOne.Name,
                        taste,
                        tasteLabel = TasteLabel(taste),
                        friendshipBefore = before,
                        friendshipAfter = after,
                        delta = after - before
                    });
                    return;
                }

                // ── 2) 目标是玩家 → 真实送礼交互（SendProposal 流程） ──
                // Farmer.checkAction：who 手持可送物品 + 站在对方旁边 → 弹"把 X 送给 小恒？"Yes/No
                // → 选 Yes → team.SendProposal(Gift) → 对方接受后物品过去（戒指也能送）。
                var targetFarmer = Game1.getAllFarmers().FirstOrDefault(f => f.Name == target);
                if (targetFarmer != null)
                {
                    if (item is not StardewValley.Object giftObj || !item.canBeGivenAsGift())
                    {
                        tcs.SetResult(new { ok = false, error = $"{item.Name} 不能作为礼物" });
                        return;
                    }

                    // 从背包取 1 个，设为手持（ActiveObject）
                    var giftOne = (StardewValley.Object)item.getOne();
                    item.Stack -= 1;
                    if (item.Stack <= 0) farmer.Items[idx] = null;
                    var prevActive = farmer.ActiveObject;
                    farmer.ActiveObject = giftOne;

                    // 站到目标下方一格，面朝目标（面前格 = 目标所在格）
                    var tt = targetFarmer.TilePoint;
                    ClearMovementState();
                    farmer.Position = new Vector2(tt.X, tt.Y + 1) * Game1.tileSize;
                    farmer.FacingDirection = 0;
                    CenterViewportOnFarmer(farmer);

                    // 触发 checkAction（面前格）→ 弹送礼确认对话框
                    var facing = GetFacingTile(farmer);
                    bool triggered = loc.checkAction(
                        new Location((int)facing.X, (int)facing.Y),
                        Game1.viewport,
                        farmer);
                    if (!triggered)
                    {
                        farmer.ActiveObject = prevActive;
                        farmer.addItemToInventory(giftOne);
                        tcs.SetResult(new { ok = false, error = "没触发送礼交互（目标不在面前或不可送）" });
                        return;
                    }

                    // 延迟一帧点 "Yes"：responseCC 在对话框首帧 draw 后才填充，立即点会失败。
                    DelayedAction.functionAfterDelay(() =>
                    {
                        bool yesClicked = false;
                        if (Game1.activeClickableMenu is StardewValley.Menus.DialogueBox db)
                        {
                            var rf = typeof(StardewValley.Menus.DialogueBox).GetField("responseCC",
                                System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
                            var rcs = rf?.GetValue(db) as List<ClickableComponent>;
                            if (rcs != null && rcs.Count > 0)
                            {
                                db.selectedResponse = 0;
                                var rc = rcs[0];
                                db.receiveLeftClick(rc.bounds.Center.X, rc.bounds.Center.Y);
                                yesClicked = true;
                            }
                        }
                        if (!yesClicked)
                        {
                            var f2 = Game1.player;
                            f2.ActiveObject = prevActive;
                            f2.addItemToInventory(giftOne);
                            tcs.SetResult(new { ok = false, error = "送礼对话框未能确认" });
                            return;
                        }
                        tcs.SetResult(new { ok = true, action = "gift_proposal_sent", target = targetFarmer.Name, item = giftOne.Name, note = "已发送礼物提议，等待对方接受" });
                    }, 400);
                    return;
                }

                tcs.SetResult(new { ok = false, error = $"找不到目标 {target}（当前地图无此 NPC，也不在玩家列表）" });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// GET /friendship?npc=Leah
    /// 查询当前玩家与该 NPC 的好感度（点数/心/本周已送次数）。
    /// </summary>
    private object HandleFriendship(HttpListenerContext ctx)
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var qs = ctx.Request.QueryString;
        var npcName = qs["npc"] ?? "";

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            var farmer = Game1.player;
            if (!farmer.friendshipData.TryGetValue(npcName, out var f))
            {
                tcs.SetResult(new { ok = true, npc = npcName, points = 0, hearts = 0, giftsThisWeek = 0, giftsToday = 0, known = false });
                return;
            }
            tcs.SetResult(new
            {
                ok = true,
                npc = npcName,
                points = f.Points,
                hearts = f.Points / 250,
                giftsThisWeek = f.GiftsThisWeek,
                giftsToday = f.GiftsToday,
                known = true
            });
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>SDV 礼物喜好度 → 中文标签。0=最爱 2=喜欢 8=一般 4=不喜欢 6=讨厌 7=星之果茶。</summary>
    private static string TasteLabel(int taste) => taste switch
    {
        0 => "最爱",
        2 => "喜欢",
        8 => "一般",
        4 => "不喜欢",
        6 => "讨厌",
        7 => "星之果茶",
        _ => "未知"
    };

    private object HandleChest(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var cx = GetParam<int>(p, "x");
        var cy = GetParam<int>(p, "y");

        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            var farmer = Game1.player;
            var loc = farmer.currentLocation;
            var tileVec = new Vector2(cx, cy);

            if (!loc.objects.TryGetValue(tileVec, out var obj) || obj is not StardewValley.Objects.Chest chest)
            {
                tcs.SetResult(new { ok = false, error = $"No chest at ({cx},{cy})" });
                return;
            }

            var items = chest.Items
                .Where(i => i != null)
                .Select(i => new { name = i.Name, count = i.Stack })
                .ToList();

            tcs.SetResult(new { ok = true, items, capacity = chest.GetActualCapacity(), used = items.Count });
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// GET /scan_chests
    /// 扫描当前地图所有箱子，返回每个箱子的位置和物品列表。
    /// </summary>
    private object HandleScanChests()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var loc = Game1.player?.currentLocation;
                if (loc == null)
                {
                    tcs.SetResult(new { ok = false, error = "No location" });
                    return;
                }

                var chests = new List<object>();
                foreach (var (chest, tile, label) in CollectStorageChests(loc))
                {
                    var items = chest.Items
                        .Where(i => i != null)
                        .Select(i => new { name = i.Name, count = i.Stack, qualifiedId = i.QualifiedItemId })
                        .ToList();
                    int used = items.Count;
                    chests.Add(new
                    {
                        x = (int)tile.X,
                        y = (int)tile.Y,
                        items,
                        capacity = chest.GetActualCapacity(),
                        used,
                        location = loc.Name,
                        name = DisplayChestName(chest, label),
                        color = ChestColorHex(chest),
                        freeSlots = chest.GetActualCapacity() - used
                    });
                }

                tcs.SetResult(new { ok = true, location = loc.Name, count = chests.Count, chests });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// POST /chest_take  { "x": 30, "y": 15, "name": "Stone", "count": 10 }
    /// 从箱子取出物品。不指定 count 则全部取出。
    /// </summary>
    private object HandleChestTake(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var cx = GetParam<int>(p, "x");
        var cy = GetParam<int>(p, "y");
        var name = GetParam<string>(p, "name");
        var count = GetParamOr(p, "count", int.MaxValue);

        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var farmer = Game1.player;
                var loc = farmer.currentLocation;
                var tileVec = new Vector2(cx, cy);

                if (!loc.objects.TryGetValue(tileVec, out var obj) || obj is not StardewValley.Objects.Chest chest)
                {
                    tcs.SetResult(new { ok = false, error = $"No chest at ({cx},{cy})" });
                    return;
                }

                int taken = 0;
                for (int i = 0; i < chest.Items.Count; i++)
                {
                    var item = chest.Items[i];
                    if (item == null) continue;
                    if (!item.Name.Equals(name, StringComparison.OrdinalIgnoreCase))
                        continue;

                    int want = count == int.MaxValue ? item.Stack : Math.Min(count - taken, item.Stack);
                    if (want <= 0) break;

                    var toGive = item.getOne();
                    toGive.Stack = want;

                    var leftover = farmer.addItemToInventory(toGive);
                    if (leftover != null && leftover.Stack > 0)
                    {
                        // 背包满了，放回箱子
                        chest.Items[i] = item;
                        break;
                    }

                    item.Stack -= want;
                    if (item.Stack <= 0)
                        chest.Items[i] = null;

                    taken += want;
                }

                tcs.SetResult(new
                {
                    ok = true,
                    taken,
                    item = name,
                    chestAt = new { x = cx, y = cy }
                });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    private object HandleHarvest(HttpListenerContext ctx)
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var qs = ctx.Request.QueryString;
        int radius = 15;
        if (int.TryParse(qs["radius"], out var r) && r > 0 && r <= 50)
            radius = r;

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            var farmer = Game1.player;
            var loc = farmer.currentLocation;
            int count = 0;

            foreach (var pair in loc.terrainFeatures.Pairs)
            {
                if (pair.Value is HoeDirt dirt && dirt.crop != null && dirt.readyForHarvest())
                {
                    var pos = pair.Key;
                    if (Math.Abs(pos.X - farmer.TilePoint.X) > radius
                        || Math.Abs(pos.Y - farmer.TilePoint.Y) > radius)
                        continue;

                    if (dirt.crop.harvest((int)pos.X, (int)pos.Y, dirt))
                    {
                        dirt.destroyCrop(false);
                        count++;
                    }
                }
            }

            tcs.SetResult(new { ok = true, harvested = count });
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    private object HandleSell(HttpListenerContext ctx)
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var p = ReadJson(ctx);
        var name = GetParamOr(p, "name", "");
        var sellAll = GetParamOr(p, "all", false);

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            var farmer = Game1.player;
            var loc = farmer.currentLocation;

            var bin = loc is Farm farm
                ? farm.getShippingBin(farmer)
                : null;

            if (bin == null)
            {
                tcs.SetResult(new { ok = false, error = "No shipping bin found (must be on Farm)" });
                return;
            }

            var sold = new List<object>();
            var keepCategories = new HashSet<int> { -99, -98, -97, -96 }; // tools, rings, boots, weapons

            for (int i = farmer.Items.Count - 1; i >= 0; i--)
            {
                var item = farmer.Items[i];
                if (item == null) continue;
                if (item is Tool) continue;
                if (keepCategories.Contains(item.Category)) continue;

                if (!sellAll && !string.IsNullOrEmpty(name)
                    && !item.Name.Equals(name, StringComparison.OrdinalIgnoreCase))
                    continue;

                if (sellAll && item.Name.Contains("Seeds", StringComparison.OrdinalIgnoreCase))
                    continue;

                var salePrice = item is StardewValley.Object obj ? obj.sellToStorePrice() * item.Stack : 0;
                sold.Add(new { item = item.Name, count = item.Stack, price = salePrice });

                bin.Add(item);
                farmer.Items[i] = null;
            }

            tcs.SetResult(new { ok = true, sold, totalItems = sold.Count });
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    private object HandleSellToShop(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var name = GetParamOr(p, "name", "");
        var count = GetParamOr(p, "count", -1);  // -1 = sell all

        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var menu = Game1.activeClickableMenu;
                if (menu is not ShopMenu shop)
                {
                    tcs.SetResult(new { ok = false, error = "No shop menu open" });
                    return;
                }

                var farmer = Game1.player;
                var sold = new List<object>();
                int totalGold = 0;

                for (int i = 0; i < farmer.Items.Count; i++)
                {
                    var item = farmer.Items[i];
                    if (item == null) continue;
                    if (!item.Name.Equals(name, StringComparison.OrdinalIgnoreCase)) continue;

                    // Get the inventory slot clickable component
                    var inventoryField = shop.GetType().GetField("inventory",
                        System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic |
                        System.Reflection.BindingFlags.Instance);
                    if (inventoryField?.GetValue(shop) is IClickableMenu inventoryMenu)
                    {
                        var invSlotsField = inventoryMenu.GetType().GetField("inventory",
                            System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic |
                            System.Reflection.BindingFlags.Instance);
                        if (invSlotsField?.GetValue(inventoryMenu) is List<ClickableComponent> slots
                            && i < slots.Count)
                        {
                            var slot = slots[i];
                            // ⚠️ SDV 商店卖：单击卖整个堆叠（inventory.leftClick → chargePlayer(-num * item.Stack)），
                            // 右键才是一个个。所以一次 receiveLeftClick 就整组卖完，count 参数游戏层面不生效。
                            // 想卖部分：先 chest_take(N) 把 N 个拿出来再整组卖。
                            int unitPrice = item is StardewValley.Object obj ? obj.sellToStorePrice() : 0;
                            int stackBefore = item.Stack;
                            shop.receiveLeftClick(slot.bounds.Center.X, slot.bounds.Center.Y);

                            sold.Add(new { item = item.Name, sold = stackBefore, unitPrice, totalPrice = unitPrice * stackBefore });
                            totalGold += unitPrice * stackBefore;
                        }
                    }
                    break;  // Only process first matching stack
                }

                if (sold.Count == 0)
                    tcs.SetResult(new { ok = false, error = $"Item '{name}' not found in inventory" });
                else
                    tcs.SetResult(new { ok = true, sold, totalGold, remainingGold = farmer.Money });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    private object HandleRefill()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            var wc = Game1.player.Items.OfType<WateringCan>().FirstOrDefault();
            if (wc == null)
            {
                tcs.SetResult(new { ok = false, error = "No watering can in inventory" });
                return;
            }
            wc.WaterLeft = wc.waterCanMax;
            tcs.SetResult(new { ok = true, water = wc.WaterLeft, max = wc.waterCanMax });
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    private object HandleHeal()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            var f = Game1.player;
            f.health = f.maxHealth;
            f.Stamina = f.MaxStamina;
            tcs.SetResult(new { ok = true, health = f.health, stamina = f.Stamina });
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// POST /eat — 吃掉当前选中的食物（回血/回体力）。
    /// 2026-08-06：/use 对食物是放置(placementAction)不是吃，矿里没血只能撤退。
    /// eatObject 会自动减 1 个。
    /// </summary>
    private object HandleEat()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var farmer = Game1.player;
                var item = farmer.CurrentItem;
                if (item is StardewValley.Object obj && obj.Edibility > 0)
                {
                    // 原版吃法：eatObject 播动画+游戏自动回血+buff，调用方吃完等动画（~2s）再继续
                    farmer.eatObject(obj, true);
                    obj.Stack--;
                    if (obj.Stack <= 0)
                    {
                        int idx = farmer.Items.IndexOf(obj);
                        if (idx >= 0)
                            farmer.Items[idx] = null;
                    }
                    tcs.SetResult(new { ok = true, ate = item.Name, health = farmer.health,
                        stamina = (int)farmer.Stamina });
                }
                else
                {
                    tcs.SetResult(new { ok = false, error = "当前物品不可食用（先 /select 选个食物）" });
                }
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    private object HandleRipen(HttpListenerContext ctx)
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var qs = ctx.Request.QueryString;
        int radius = 30;
        if (int.TryParse(qs["radius"], out var r) && r > 0 && r <= 50)
            radius = r;

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            var farmer = Game1.player;
            var loc = farmer.currentLocation;
            int count = 0;

            foreach (var pair in loc.terrainFeatures.Pairs)
            {
                if (pair.Value is HoeDirt dirt && dirt.crop != null && !dirt.readyForHarvest())
                {
                    var pos = pair.Key;
                    if (Math.Abs(pos.X - farmer.TilePoint.X) <= radius
                        && Math.Abs(pos.Y - farmer.TilePoint.Y) <= radius)
                    {
                        dirt.crop.growCompletely();
                        count++;
                    }
                }
            }

            tcs.SetResult(new { ok = true, ripened = count });
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    private object HandleGive(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var itemId = GetParam<string>(p, "id");
        var count = GetParamOr(p, "count", 1);
        var upgrade = GetParamOr(p, "upgrade", 0);  // 工具升级等级（如4=铱）

        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            var farmer = Game1.player;
            var item = ItemRegistry.Create(itemId, count);
            if (item is Tool tool && upgrade > 0)
                tool.UpgradeLevel = upgrade;  // 作弊给升级工具
            farmer.addItemToInventory(item);
            tcs.SetResult(new { ok = true, given = item.Name, count, id = itemId, upgrade });
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    private object HandleMoney(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var amount = GetParam<int>(p, "amount");

        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            Game1.player.Money += amount;
            tcs.SetResult(new { ok = true, added = amount, total = Game1.player.Money });
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    private object HandlePause()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");
        _frozenTime = Game1.timeOfDay;
        _timeFrozen = true;
        return new { ok = true, action = "paused", frozenAt = _frozenTime };
    }

    private object HandleResume()
    {
        _timeFrozen = false;
        return new { ok = true, action = "resumed" };
    }

    private object HandleFishbot(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var action = GetParamOr(p, "action", "toggle"); // on, off, toggle, status

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                // Find Fishbot mod via SMAPI mod registry
                object? fishbotMod = null;
                System.Reflection.FieldInfo? autoField = null;
                System.Reflection.PropertyInfo? autoProp = null;

                // 🔧 2026-08-23 恒：反射耦合脆弱，Fishbot 升级改成员名就断。先抓版本用于报错/自检。
                var modInfo = this.Helper.ModRegistry.Get("AdroSlice.Fishbot");
                string modVersion = modInfo?.Manifest.Version?.ToString() ?? "?";
                if (modInfo != null)
                {
                    var modInfoType = modInfo.GetType();
                    var modProp = modInfoType.GetProperty("Mod",
                        System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic |
                        System.Reflection.BindingFlags.Instance);
                    fishbotMod = modProp?.GetValue(modInfo);
                    if (fishbotMod == null)
                    {
                        var modField = modInfoType.GetField("Mod",
                            System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic |
                            System.Reflection.BindingFlags.Instance);
                        fishbotMod = modField?.GetValue(modInfo);
                    }
                }

                if (fishbotMod == null)
                {
                    tcs.SetResult(new { ok = false, found = false,
                        error = "Fishbot mod not found（没装 / UniqueID 不是 AdroSlice.Fishbot）" });
                    return;
                }

                // Find the toggle member (AutomationEnabled) — 多候选兜底，Fishbot 改过名也能找到。
                var fbType = fishbotMod.GetType();
                FindFishbotToggle(fbType, out autoField, out autoProp);

                var bindingAll = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic |
                    System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Static;

                if (autoField != null || autoProp != null)
                {
                    bool current = autoField != null
                        ? (bool)autoField.GetValue(fishbotMod)!
                        : (bool)autoProp!.GetValue(fishbotMod)!;
                    bool target = action == "toggle" ? !current : action == "on";

                    if (action != "status")
                    {
                        if (autoField != null) autoField.SetValue(fishbotMod, target);
                        else autoProp!.SetValue(fishbotMod, target);

                        if (target)
                        {
                            var startMethod = fbType.GetMethod("StartCasting", bindingAll)
                                ?? fbType.GetMethod("StartFishing", bindingAll)
                                ?? fbType.GetMethod("BeginCasting", bindingAll);
                            startMethod?.Invoke(fishbotMod, null);
                        }
                        else
                        {
                            var resetMethod = fbType.GetMethod("reset", bindingAll)
                                ?? fbType.GetMethod("Reset", bindingAll);
                            resetMethod?.Invoke(fishbotMod, null);
                        }
                    }
                    tcs.SetResult(new { ok = true, enabled = action == "status" ? current : target });
                }
                else
                {
                    // List all fields for debugging
                    var fields = fbType.GetFields(
                        System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic |
                        System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Static);
                    var names = string.Join(", ", fields.Select(f => f.Name));
                    tcs.SetResult(new { ok = false, found = true, version = modVersion,
                        error = $"Fishbot {modVersion} 找不到可控开关(AutomationEnabled/app)/启动方法——版本可能已变更，" +
                                $"请更新 NagiBridge 或换回此版本 Fishbot。可用字段: {names}" });
                }
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    // 🔧 2026-08-23 恒：Fishbot 的 AutomationEnabled 是私有成员，反射耦合脆弱——改过名/升级后 /fishbot 会断。
    // 这里用「候选名 + 布尔成员名扫描」兜底，尽量兼容 Fishbot 后续版本；真找不到也给出可操作报错（带版本）。
    private static void FindFishbotToggle(System.Type fbType, out System.Reflection.FieldInfo? field, out System.Reflection.PropertyInfo? prop)
    {
        var flags = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic |
                    System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Static;
        string[] preferred = { "AutomationEnabled", "AutoFishEnabled", "FishAutomationEnabled",
                               "EnableAutomation", "Enabled" };
        foreach (var name in preferred)
        {
            var f = fbType.GetField(name, flags);
            if (f != null && f.FieldType == typeof(bool)) { field = f; prop = null; return; }
            var pp = fbType.GetProperty(name, flags);
            if (pp != null && pp.PropertyType == typeof(bool) && pp.CanRead && pp.CanWrite)
            { field = null; prop = pp; return; }
        }
        // 兜底：扫描所有布尔成员，名字含 auto/fish 的优先（Fishbot 的相关开关命名含这些词）
        foreach (var f in fbType.GetFields(flags))
            if (f.FieldType == typeof(bool) && _NameMentionsFish(f.Name)) { field = f; prop = null; return; }
        foreach (var pp in fbType.GetProperties(flags))
            if (pp.PropertyType == typeof(bool) && pp.CanRead && pp.CanWrite && _NameMentionsFish(pp.Name))
            { field = null; prop = pp; return; }
        field = null; prop = null;
    }

    private static bool _NameMentionsFish(string name)
    {
        return name.IndexOf("auto", System.StringComparison.OrdinalIgnoreCase) >= 0 ||
               name.IndexOf("fish", System.StringComparison.OrdinalIgnoreCase) >= 0;
    }

    // (PrairieKing bot 已移除 2026-08-12：联机下波次切换卡死，按恒意见删除)

    private object HandleMenu()
    {
        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var menu = Game1.activeClickableMenu;
                if (menu == null)
                {
                    object? eventInfo = null;
                    if (Game1.currentLocation?.currentEvent != null)
                    {
                        var ev = Game1.currentLocation.currentEvent;
                        eventInfo = new { id = ev.id, skippable = ev.skippable };
                    }
                    tcs.SetResult(new { ok = true, open = false, activeEvent = eventInfo });
                    return;
                }

                var menuType = menu.GetType().Name;
                string? dialogue = null;
                List<object>? responses = null;
                List<object>? shopItems = null;
                List<object>? buttons = null;
                List<object>? grabItems = null;
                List<object>? grabSlots = null;
                string? letterTitle = null;
                string? letterBody = null;
                string? letterFrom = null;
                bool menuIsChoice = false;
                object? shopPage = null;
                object? ccInfo = null;
                bool giftMenu = false;   // 🎁 ItemGrabMenu+reverseGrab/behaviorFunction（送礼菜单，点物品=送出不是拿起）

                if (menu is DialogueBox db)
                {
                    try { dialogue = db.getCurrentString(); } catch { }

                    var responseField = typeof(DialogueBox).GetField("responseCC",
                        System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic |
                        System.Reflection.BindingFlags.Instance);
                    var responseCCs = responseField?.GetValue(db) as List<ClickableComponent>;

                    var responsesField = typeof(DialogueBox).GetField("responses",
                        System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic |
                        System.Reflection.BindingFlags.Instance);
                    // ⚠️ SDV 1.6 的 responses 是 Response[] 不是 List<Response>，用 IEnumerable 兼容两种
                    var responseArr = responsesField?.GetValue(db) as IEnumerable<Response>;

                    if (responseArr != null)
                    {
                        var responseList = responseArr.ToList();
                        if (responseList.Count > 0)
                        {
                            responses = new List<object>();
                            for (int i = 0; i < responseList.Count; i++)
                            {
                                var r = responseList[i];
                                responses.Add(new
                                {
                                    index = i,
                                    key = r.responseKey,
                                    text = r.responseText,
                                    bounds = responseCCs != null && i < responseCCs.Count
                                        ? new { x = responseCCs[i].bounds.X, y = responseCCs[i].bounds.Y,
                                                w = responseCCs[i].bounds.Width, h = responseCCs[i].bounds.Height }
                                        : null
                                });
                            }
                        }
                    }
                }
                else if (menu is ShopMenu shop)
                {
                    shopItems = new List<object>();
                    var forSale = shop.forSale;
                    var itemPriceAndStock = shop.itemPriceAndStock;
                    var saleButtons = shop.forSaleButtons;
                    // SDV 1.6 商店用右侧 up/down 箭头 + 滚动条分页（每页4个），
                    // forSaleButtons 永远是当前页的4个按钮，但显示的是 forSale[currentItemIndex+i]。
                    // bounds 必须按 currentItemIndex 偏移，否则滚动后坐标错位。
                    int pageOffset = shop.currentItemIndex;
                    for (int i = 0; i < forSale.Count; i++)
                    {
                        var item = forSale[i];
                        int price = 0;
                        int stock = -1;
                        string? trade = null;
                        int? tradeCount = null;
                        string? tradeName = null;
                        if (itemPriceAndStock.TryGetValue(item, out var info))
                        {
                            price = info.Price;
                            stock = info.Stock;
                            // 🆕 2026-08-18 升级工具材料需求（SDV1.6 ItemStockInformation.TradeItem/TradeItemCount）
                            trade = info.TradeItem;
                            tradeCount = info.TradeItemCount;
                            try { tradeName = trade != null ? (ItemRegistry.Create(trade, 1)?.DisplayName) : null; } catch { }
                        }
                        int btnIndex = i - pageOffset;
                        shopItems.Add(new
                        {
                            name = item.DisplayName,
                            id = item.QualifiedItemId,
                            price,
                            stock,
                            trade,
                            tradeCount,
                            tradeName,
                            visible = btnIndex >= 0 && btnIndex < saleButtons.Count,
                            // 商店格子点击坐标（取件/购买用；梳妆台/鱼缸/马龙/罗宾全适用）
                            bounds = saleButtons != null && btnIndex >= 0 && btnIndex < saleButtons.Count
                                ? new { x = saleButtons[btnIndex].bounds.Center.X, y = saleButtons[btnIndex].bounds.Center.Y }
                                : null
                        });
                    }

                    // 商店分页状态（AI 判断要不要点 up/down 箭头翻页）
                    shopPage = new
                    {
                        index = shop.currentItemIndex,
                        pageSize = 4,
                        total = forSale.Count
                    };

                    // 商店菜单的背包侧槽位（放物品进衣柜/商店用）：InventoryMenu.inventory
                    if (shop.inventory?.inventory != null)
                    {
                        grabSlots = new List<object>();
                        for (int i = 0; i < shop.inventory.inventory.Count; i++)
                        {
                            var cc = shop.inventory.inventory[i];
                            if (cc == null) continue;
                            grabSlots.Add(new
                            {
                                index = i,
                                x = cc.bounds.Center.X,
                                y = cc.bounds.Center.Y,
                                w = cc.bounds.Width,
                                h = cc.bounds.Height
                            });
                        }
                        if (grabSlots.Count == 0) grabSlots = null;
                    }
                }
                else if (menu is GameMenu gm)
                {
                    // 背包/技能/社交等菜单：报当前页的背包槽位 + 垃圾桶（整理背包用）。
                    // GameMenu 的 inventory 和 trashCan 在 InventoryPage（私有嵌套类）里，用反射取。
                    var bFlags = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic
                        | System.Reflection.BindingFlags.Instance;
                    IClickableMenu? page = gm.currentTab >= 0 && gm.currentTab < gm.pages.Count
                        ? gm.pages[gm.currentTab] : null;
                    if (page != null)
                    {
                        // 背包槽位（InventoryPage.inventory 是 InventoryMenu）
                        var invField = page.GetType().GetField("inventory", bFlags);
                        if (invField?.GetValue(page) is InventoryMenu im && im.inventory != null)
                        {
                            grabSlots = new List<object>();
                            for (int i = 0; i < im.inventory.Count; i++)
                            {
                                var cc = im.inventory[i];
                                if (cc == null) continue;
                                grabSlots.Add(new
                                {
                                    index = i,
                                    x = cc.bounds.Center.X,
                                    y = cc.bounds.Center.Y,
                                    w = cc.bounds.Width,
                                    h = cc.bounds.Height
                                });
                            }
                            if (grabSlots.Count == 0) grabSlots = null;
                        }
                        // 垃圾桶（InventoryPage.trashCan）
                        var trashField = page.GetType().GetField("trashCan", bFlags);
                        if (trashField?.GetValue(page) is ClickableTextureComponent trashCan && trashCan.visible)
                        {
                            buttons ??= new List<object>();
                            buttons.Add(new
                            {
                                name = "trashCan",
                                x = trashCan.bounds.Center.X,
                                y = trashCan.bounds.Center.Y
                            });
                        }
                    }
                }
                else if (menu.GetType().Name == "ChooseFromIconsMenu")
                {
                    // 选效果菜单（矮人国王雕像等）：选项在 answerChoices(Response)，
                    // 图标可能在任意 List<ClickableComponent> 字段——穷举探测。
                    var bFlags = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic
                        | System.Reflection.BindingFlags.Instance;

                    // 1) answerChoices / questionChoices 的文本选项
                    responses = new List<object>();
                    foreach (var fname in new[] { "answerChoices", "questionChoices" })
                    {
                        var f = menu.GetType().GetField(fname, bFlags);
                        if (f?.GetValue(menu) is System.Collections.IEnumerable qs)
                        {
                            int i = 0;
                            foreach (var q in qs.OfType<Response>())
                            {
                                responses.Add(new { index = i++, key = q.responseKey, text = q.responseText });
                            }
                        }
                    }
                    if (responses.Count == 0) responses = null;

                    // 2) 穷举所有 List<ClickableComponent> 字段当图标按钮
                    var choices = new List<object>();
                    foreach (var f in menu.GetType().GetFields(bFlags))
                    {
                        if (f.GetValue(menu) is System.Collections.IEnumerable en
                            && en.OfType<ClickableTextureComponent>().Any())
                        {
                            foreach (var ic in en.OfType<ClickableTextureComponent>())
                            {
                                choices.Add(new
                                {
                                    field = f.Name,
                                    name = ic.name,
                                    hoverText = ic.hoverText,
                                    x = ic.bounds.Center.X,
                                    y = ic.bounds.Center.Y
                                });
                            }
                        }
                    }
                    if (choices.Count > 0)
                    {
                        buttons = choices;
                        menuIsChoice = true;
                    }
                }

                else if (menu is SpecialOrdersBoard sob)
                {
                    // 🆕 2026-08-18 沙漠节马龙任务板：序列化左右任务卡（SpecialOrder）到 items
                    var sobFlags = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic
                        | System.Reflection.BindingFlags.Instance;
                    var lo = typeof(SpecialOrdersBoard).GetField("leftOrder", sobFlags)?.GetValue(sob)
                        as StardewValley.SpecialOrders.SpecialOrder;
                    var ro = typeof(SpecialOrdersBoard).GetField("rightOrder", sobFlags)?.GetValue(sob)
                        as StardewValley.SpecialOrders.SpecialOrder;
                    var btnL = typeof(SpecialOrdersBoard).GetField("acceptLeftQuestButton", sobFlags)?.GetValue(sob)
                        as ClickableComponent;
                    var btnR = typeof(SpecialOrdersBoard).GetField("acceptRightQuestButton", sobFlags)?.GetValue(sob)
                        as ClickableComponent;
                    var boardOrders = new List<object>();
                    foreach (var (order, acceptBtn) in new[] { (lo, btnL), (ro, btnR) })
                    {
                        if (order == null) continue;
                        string? name = null, desc = null;
                        List<string>? objs = null;
                        try { name = order.GetName(); } catch { }
                        try { desc = order.GetDescription(); } catch { }
                        try { objs = order.GetObjectiveDescriptions(); } catch { }
                        bool accepted = false;
                        bool btnVisible = false;
                        try { accepted = Game1.player.team.specialOrders.Contains(order); } catch { }
                        try { btnVisible = acceptBtn != null && acceptBtn.visible; } catch { }
                        // 🆕 奖励描述（沙漠节任务=卡利科蛋 ObjectReward，2026-08-18 反编译 OrderReward 子类）
                        var rwDescs = new List<string>();
                        try
                        {
                            foreach (var rw in order.rewards)
                            {
                                if (rw is StardewValley.SpecialOrders.Rewards.ObjectReward objRw)
                                {
                                    string iname = "?";
                                    try { iname = objRw.itemKey?.Value ?? "?"; } catch { }
                                    try { iname = ItemRegistry.Create(iname, 1)?.DisplayName ?? iname; } catch { }
                                    int amt = 0;
                                    try { amt = objRw.amount?.Value ?? 0; } catch { }
                                    rwDescs.Add($"{iname}×{amt}");
                                }
                                else if (rw is StardewValley.SpecialOrders.Rewards.MoneyReward mn)
                                {
                                    int amt2 = 0;
                                    try { amt2 = mn.amount?.Value ?? 0; } catch { }
                                    rwDescs.Add($"💰{amt2}g");
                                }
                                else if (rw is StardewValley.SpecialOrders.Rewards.GemsReward gemRw)
                                {
                                    int amt3 = 0;
                                    try { amt3 = gemRw.amount?.Value ?? 0; } catch { }
                                    rwDescs.Add($"💎{amt3}齐钻");
                                }
                                else if (rw != null)
                                    rwDescs.Add(rw.ToString() ?? "?");
                            }
                        }
                        catch { }
                        string soId = "";
                        string soKey = "";
                        try { soId = ReflectField(order, "orderId"); } catch { }
                        try { soKey = order.questKey?.Value ?? ""; } catch { }
                        boardOrders.Add(new
                        {
                            orderId = soId,
                            questKey = soKey,
                            name = name ?? order.questName?.Value ?? "?",
                            description = desc,
                            objectives = objs,
                            moneyReward = order.GetMoneyReward(),
                            rewards = rwDescs,
                            daysLeft = order.GetDaysLeft(),
                            requester = order.requester?.Value ?? "",
                            accepted,
                            canAccept = !accepted && btnVisible
                        });
                    }
                    grabItems = boardOrders;
                }

                else if (menu is ForgeMenu)
                {
                    // 锻造台（附魔/幻化/组合戒指）：穷举 ClickableComponent 字段拿
                    // 左/右物品槽、宝石槽、锻造/拆解/OK/垃圾桶按钮坐标（field 名标识用途）。
                    // ⚠️ 2026-08-10 加 null 组件名报告：unforgeButton 等字段可能为 null/普通 ClickableComponent，
                    //    报出来让 AI/MCP 知道有哪些交互点。附魔槽内容读不了就报坐标。
                    var fFlags = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic
                        | System.Reflection.BindingFlags.Instance;
                    var forgeCCs = new List<object>();
                    var nullCCs = new List<string>();
                    foreach (var f in menu.GetType().GetFields(fFlags))
                    {
                        var val = f.GetValue(menu);
                        if (val is ClickableComponent fcc && fcc != null)
                        {
                            forgeCCs.Add(new
                            {
                                field = f.Name,
                                name = fcc.name ?? "",
                                x = fcc.bounds.Center.X,
                                y = fcc.bounds.Center.Y,
                                w = fcc.bounds.Width,
                                h = fcc.bounds.Height
                            });
                        }
                        else if (val == null
                                 && (typeof(ClickableComponent).IsAssignableFrom(f.FieldType)))
                        {
                            nullCCs.Add(f.Name);  // 组件字段但当前 null（如 unforgeButton 未激活）
                        }
                    }
                    if (forgeCCs.Count > 0)
                    {
                        buttons = forgeCCs;
                        menuIsChoice = true;  // 让 AI 知道这是可点击交互菜单
                    }
                    if (nullCCs.Count > 0)
                    {
                        buttons ??= new List<object>();
                        buttons.Add(new { field = "__nullComponents", name = string.Join(",", nullCCs), x = 0, y = 0 });
                    }
                    // 槽内容：反射扫 Item 字段（leftIngredient/rightIngredient/gem 等）报里面放了啥
                    // 恒 2026-08-10：合成后光标还拿着结果，AI 不知道 → 报 heldItem 已在 /state；
                    //   这里报锻造槽里当前放了什么材料，AI 操作前能确认。
                    var forgeItems = new List<object>();
                    foreach (var f in menu.GetType().GetFields(fFlags))
                    {
                        try
                        {
                            var fv = f.GetValue(menu);
                            if (fv is Item fi && fi != null)
                            {
                                forgeItems.Add(new { field = f.Name, name = fi.Name, stack = fi.Stack });
                            }
                            else if (fv is Netcode.NetRef<Item> netItem && netItem.Value != null)
                            {
                                forgeItems.Add(new { field = f.Name, name = netItem.Value.Name, stack = netItem.Value.Stack });
                            }
                            else if (fv is ClickableComponent cc2 && cc2.item != null)
                            {
                                // 槽内容挂在组件.item 上（leftIngredientSpot.item 等）——2026-08-10 补
                                forgeItems.Add(new { field = f.Name, name = cc2.item.Name, stack = cc2.item.Stack });
                            }
                        }
                        catch { }
                    }
                    if (forgeItems.Count > 0)
                        shopItems = forgeItems;  // 复用 shopItems 字段带出槽内容
                    // 底部背包槽位（InventoryMenu）
                    foreach (var f in menu.GetType().GetFields(fFlags))
                    {
                        if (f.GetValue(menu) is InventoryMenu fim && fim.inventory != null)
                        {
                            grabSlots = new List<object>();
                            for (int i = 0; i < fim.inventory.Count; i++)
                            {
                                var fcc2 = fim.inventory[i];
                                if (fcc2 == null) continue;
                                grabSlots.Add(new { index = i, x = fcc2.bounds.Center.X, y = fcc2.bounds.Center.Y });
                            }
                            if (grabSlots.Count == 0) grabSlots = null;
                            break;
                        }
                    }
                }

                else if (menu is ItemGrabMenu igm)
                {
                    // 🎁 送礼菜单（冬星节神秘礼物等）：reverseGrab 或 behaviorFunction 设置过
                    // → 点物品=送出（ItemGrabMenu.receiveLeftClick 内部调 behaviorFunction），不是拿起。
                    giftMenu = igm.reverseGrab || igm.behaviorFunction != null;
                    // 领取物菜单（吉尔讨伐奖励/宝箱）：报出可领取物品 + 槽位坐标
                    // SDV 1.6 各版本字段名不同——用反射穷举 Item 字段和 ClickableComponent 槽位字段
                    grabItems = new List<object>();
                    grabSlots = new List<object>();
                    var gFlags = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic
                        | System.Reflection.BindingFlags.Instance;
                    foreach (var f in igm.GetType().GetFields(gFlags))
                    {
                        object? fv = null;
                        try { fv = f.GetValue(igm); } catch { }
                        if (fv is System.Collections.IEnumerable en)
                        {
                            var items = en.OfType<Item>().ToList();
                            if (items.Count == 0) continue;
                            int idx = 0;
                            foreach (var it in items)
                            {
                                if (it != null)
                                    grabItems.Add(new { index = idx, field = f.Name,
                                        name = it.DisplayName, id = it.QualifiedItemId, stack = it.Stack });
                                idx++;
                            }
                        }
                    }
                    foreach (var f in igm.GetType().GetFields(gFlags))
                    {
                        object? fv = null;
                        try { fv = f.GetValue(igm); } catch { }
                        if (fv is System.Collections.IEnumerable en2)
                        {
                            var ccs = en2.OfType<ClickableComponent>().ToList();
                            if (ccs.Count == 0) continue;
                            foreach (var cc in ccs)
                            {
                                if (cc.visible)
                                    grabSlots.Add(new { field = f.Name, index = grabSlots.Count,
                                        x = cc.bounds.Center.X, y = cc.bounds.Center.Y });
                            }
                        }
                    }
                }

                else if (menu is LetterViewerMenu lvm)
                {
                    // 信件视图（农场信箱/公会讨伐板等）：报出信件正文（mailMessage 是 List<string> 按行存）
                    try { letterTitle = lvm.mailTitle ?? ""; } catch { }
                    try
                    {
                        if (lvm.mailMessage is List<string> lines)
                            letterBody = string.Join("\n", lines);
                        else
                            letterBody = lvm.mailMessage?.ToString() ?? "";
                    }
                    catch { }
                    letterFrom = "";  // SDV 1.6 无 from 字段
                }
                else if (menu is StardewValley.Menus.CharacterCustomization cc)
                {
                    // 捏人弹窗（新 farmhand 起名/喜好/形象）：报三个文本框内容 + canLeaveMenu，
                    // AI 判断还缺什么、用 /character_customize 填、够格就 /menu/click button=ok 确认。
                    var cFlags = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic
                        | System.Reflection.BindingFlags.Instance;
                    TextBox? GetBox(string f) => cc.GetType().GetField(f, cFlags)?.GetValue(cc) as TextBox;
                    var nb = GetBox("nameBox");
                    var fnb = GetBox("farmnameBox");
                    var fvb = GetBox("favThingBox");
                    bool canLeave = cc.canLeaveMenu();
                    ccInfo = new
                    {
                        name = nb?.Text ?? "",
                        farmname = fnb?.Text ?? "",
                        favorite = fvb?.Text ?? "",
                        canLeaveMenu = canLeave,
                        hint = canLeave
                            ? "✅ 可确认：/menu/click button=ok"
                            : "⚠️ 还缺名字/农场名/喜欢的东西——用 /character_customize {name, farmname, favorite} 填"
                    };
                }

                else if (menu.GetType().Name == "JunimoNoteMenu")
                {
                    // 献祭板菜单（2026-08-16 反编译 JunimoNoteMenu）：报当前房间 whichArea +
                    // 该房间 bundle 列表（完成状态 + 需要的物品）。AI 据此知道做什么献祭、还缺什么。
                    // 反编译字段：whichArea(int)、bundles(List<Bundle>)、Bundle.complete/bundleIndex/ingredients、
                    // BundleIngredientDescription.id/stack/quality(+GetDisplayName)。
                    var jFlags = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic
                        | System.Reflection.BindingFlags.Instance;
                    string[] areaNames = { "工艺室", "茶水间", "鱼缸", "锅炉房", "布告栏", "金库" };
                    int whichArea = 0;
                    try { whichArea = Convert.ToInt32(menu.GetType().GetField("whichArea", jFlags)?.GetValue(menu) ?? 0); } catch { }
                    var bundlesList = menu.GetType().GetField("bundles", jFlags)?.GetValue(menu) as System.Collections.IEnumerable;
                    var bundleInfos = new List<object?>();
                    if (bundlesList != null)
                    {
                        foreach (var b in bundlesList)
                        {
                            var bt = b.GetType();
                            bool complete = false; int bIndex = -1;
                            try { complete = Convert.ToBoolean(bt.GetField("complete", jFlags)?.GetValue(b) ?? false); } catch { }
                            try { bIndex = Convert.ToInt32(bt.GetField("bundleIndex", jFlags)?.GetValue(b) ?? -1); } catch { }
                            var ingredients = bt.GetField("ingredients", jFlags)?.GetValue(b) as System.Collections.IEnumerable;
                            var ingInfos = new List<object?>();
                            if (ingredients != null)
                                foreach (var ing in ingredients)
                                {
                                    var it = ing.GetType();
                                    string? id = null; int stack = 0; int quality = 0; string? name = null;
                                    bool ingDone = false;
                                    try { id = it.GetField("id", jFlags)?.GetValue(ing)?.ToString(); } catch { }
                                    try { stack = Convert.ToInt32(it.GetField("stack", jFlags)?.GetValue(ing) ?? 0); } catch { }
                                    try { quality = Convert.ToInt32(it.GetField("quality", jFlags)?.GetValue(ing) ?? 0); } catch { }
                                    try { ingDone = Convert.ToBoolean(it.GetField("completed", jFlags)?.GetValue(ing) ?? false); } catch { }
                                    try
                                    {
                                        var nm = it.GetMethod("GetDisplayName");
                                        if (nm != null) name = nm.Invoke(ing, null)?.ToString();
                                    }
                                    catch { }
                                    if (name == null && !string.IsNullOrEmpty(id))
                                    {
                                        try { name = StardewValley.ItemRegistry.Create(id)?.DisplayName; } catch { name = id; }
                                    }
                                    ingInfos.Add(new { id, name, count = stack, quality, completed = ingDone });
                                }
                            bundleInfos.Add(new { index = bIndex, complete, ingredients = ingInfos });
                        }
                    }
                    // 2026-08-16 捐赠流程扩展：specific 页坐标（bundle bounds / ingredientSlots / inventory / heldItem）
                    bool specific = false;
                    try { specific = Convert.ToBoolean(menu.GetType().GetField("specificBundlePage", jFlags)?.GetValue(menu) ?? false); } catch { }
                    // bundle 列表页点击 bounds（Bundle.bounds Rectangle）
                    var bundleBounds = new List<object?>();
                    if (bundlesList != null)
                        foreach (var b in bundlesList)
                        {
                            try
                            {
                                if (b.GetType().GetField("bounds", jFlags)?.GetValue(b) is Microsoft.Xna.Framework.Rectangle br)
                                    bundleBounds.Add(new { x = br.X, y = br.Y, w = br.Width, h = br.Height });
                            }
                            catch { }
                        }
                    // specific 页 ingredientSlots bounds
                    var ingSlots = new List<object?>();
                    try
                    {
                        var slots = menu.GetType().GetField("ingredientSlots", jFlags)?.GetValue(menu) as System.Collections.IEnumerable;
                        if (slots != null)
                            foreach (var slot in slots)
                            {
                                try
                                {
                                    if (slot.GetType().GetField("bounds", jFlags)?.GetValue(slot) is Microsoft.Xna.Framework.Rectangle sr)
                                        ingSlots.Add(new { x = sr.X, y = sr.Y, w = sr.Width, h = sr.Height });
                                }
                                catch { }
                            }
                    }
                    catch { }
                    // specific 页 inventory 槽位（底部背包，点物品拿起 heldItem）
                    var invSlots = new List<object?>();
                    try
                    {
                        var inv = menu.GetType().GetField("inventory", jFlags)?.GetValue(menu);
                        var invComps = inv?.GetType().GetField("inventory", jFlags)?.GetValue(inv) as System.Collections.IEnumerable;
                        if (invComps != null)
                            foreach (var cc0 in invComps)
                            {
                                try
                                {
                                    var cct = cc0.GetType();
                                    if (cct.GetField("bounds", jFlags)?.GetValue(cc0) is Microsoft.Xna.Framework.Rectangle cr)
                                    {
                                        string? itName = null;
                                        try { itName = (cct.GetField("item", jFlags)?.GetValue(cc0) as Item)?.DisplayName; } catch { }
                                        invSlots.Add(new { x = cr.X, y = cr.Y, w = cr.Width, h = cr.Height, item = itName });
                                    }
                                }
                                catch { }
                            }
                    }
                    catch { }
                    // heldItem（光标物品）
                    string? heldName = null;
                    try { heldName = (menu.GetType().GetField("heldItem", jFlags)?.GetValue(menu) as Item)?.DisplayName; } catch { }
                    ccInfo = new
                    {
                        whichArea,
                        areaName = (whichArea >= 0 && whichArea < areaNames.Length) ? areaNames[whichArea] : whichArea.ToString(),
                        bundles = bundleInfos,
                        specificBundlePage = specific,
                        bundleBounds,        // 列表页每块 bundle 的点击位（menu_click 进 specific 页）
                        ingredientSlots = ingSlots,  // specific 页投放槽位
                        inventorySlots = invSlots,   // specific 页底部背包（点物品拿起）
                        heldItem = heldName
                    };
                }

                // Collect named buttons via reflection（已从选效果菜单拿到选项则跳过，别覆盖）
                if (buttons == null)
                {
                buttons = new List<object>();
                foreach (var fieldName in new[] { "okButton", "cancelButton", "backButton",
                    "forwardButton", "upperRightCloseButton", "trashCan",
                    "upArrow", "downArrow", "scrollBar",
                    "areaNextButton", "areaBackButton", "purchaseButton",
                    // 🆕 2026-08-18 任务板接取按钮（SpecialOrdersBoard）
                    "acceptLeftQuestButton", "acceptRightQuestButton" })
                {
                    var field = menu.GetType().GetField(fieldName,
                        System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic |
                        System.Reflection.BindingFlags.Instance);
                    var comp = field?.GetValue(menu) as ClickableComponent;
                    if (comp != null && comp.visible)
                    {
                        buttons.Add(new
                        {
                            name = fieldName,
                            x = comp.bounds.Center.X,
                            y = comp.bounds.Center.Y
                        });
                    }
                }
                }

                // 🎁 满包接鱼/箱子领取：读领取侧 actualInventory 真物品进 grabItems（恒 2026-08-23 治本读端，AI 才能看待领取做取舍）
                if (menu is ItemGrabMenu igmRead)
                {
                    var gi = igmRead.ItemsToGrabMenu?.actualInventory;
                    if (gi != null)
                    {
                        var gl = new List<object>();
                        for (int i = 0; i < gi.Count; i++)
                            if (gi[i] != null)
                                gl.Add(new
                                {
                                    index = i,
                                    name = gi[i].DisplayName ?? gi[i].Name,
                                    count = gi[i].Stack,
                                    quality = (gi[i] as StardewValley.Object)?.Quality ?? 0,
                                    id = gi[i].QualifiedItemId
                                });
                        if (gl.Count > 0) grabItems = gl;
                    }
                }

                tcs.SetResult(new
                {
                    ok = true,
                    open = true,
                    type = menuType,
                    dialogue,
                    responses,
                    shopItems,
                    isChoice = menuIsChoice,
                    shopPage,
                    buttons = buttons != null && buttons.Count > 0 ? buttons : null,
                    items = grabItems != null && grabItems.Count > 0 ? grabItems : null,
                    slots = grabSlots != null && grabSlots.Count > 0 ? grabSlots : null,
                    letterTitle, letterBody, letterFrom,
                    characterCust = ccInfo,
                    gift = giftMenu
                });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// POST /click  { "x"?, "y"? }
    /// 屏幕点击：设鼠标到指定点（默认视口中心），有菜单则 receiveLeftClick，
    /// 无菜单（剧情演出/对话）则动作键推进（等价点击画面）。
    /// </summary>
    private object HandleClick(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var x = GetParamOr(p, "x", -1);
        var y = GetParamOr(p, "y", -1);
        // no_move=true：不调 Game1.setMousePosition（那会移 OS 光标，拉用户鼠标）。
        // 剧情推进用 receiveLeftClick(cx,cy) 传坐标即可，不需要真的移动光标。
        bool noMove = GetParamOr(p, "no_move", false);
        // no_mouse=true（2026-08-16 #5）：无菜单时**不用 OS 鼠标**（SetCursorPos+mouse_event 点屏幕中心）
        // ——后台/窗口位置变化时可能点到错误窗口（用户观察到 AI 操作到 7842）。
        // 改用进程内动作键推进：currentEvent.receiveActionPress / Game1.pressActionButton
        //（IsActive 补丁下失焦也能推进，不抢前台、不碰 OS 鼠标）。
        bool noMouse = GetParamOr(p, "no_mouse", false);

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                int cx = x >= 0 ? x : Game1.viewport.Width / 2;
                int cy = y >= 0 ? y : Game1.viewport.Height / 2;
                if (!noMove)
                    Game1.setMousePosition(cx, cy);

                if (Game1.activeClickableMenu != null)
                {
                    var menu = Game1.activeClickableMenu;
                    menu.receiveLeftClick(cx, cy, true);
                    tcs.SetResult(new { ok = true, action = "menu_click", menu = menu.GetType().Name, x = cx, y = cy });
                }
                else if (noMouse)
                {
                    // 2026-08-16 #5：不碰 OS 鼠标——进程内动作键推进（等同 /key confirm 的事件分支）
                    if (Game1.currentLocation?.currentEvent != null)
                    {
                        Game1.currentLocation.currentEvent.receiveActionPress(0, 0);
                        tcs.SetResult(new { ok = true, action = "event_advance", screen = new { x = cx, y = cy } });
                    }
                    else if (Game1.input != null)
                    {
                        Game1.pressActionButton(Game1.input.GetKeyboardState(), Game1.input.GetMouseState(), Game1.input.GetGamePadState());
                        tcs.SetResult(new { ok = true, action = "action_button", screen = new { x = cx, y = cy } });
                    }
                    else
                    {
                        tcs.SetResult(new { ok = false, error = "no input available" });
                    }
                }
                else
                {
                    // 无菜单 → 剧情演出推进：真实鼠标左键点击视口中心（跟真人点画面一样）
                    var win = Game1.game1.Window.ClientBounds;
                    int sx = win.X + win.Width / 2;
                    int sy = win.Y + win.Height / 2;
                    SetCursorPos(sx, sy);
                    mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, UIntPtr.Zero);
                    mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, UIntPtr.Zero);
                    tcs.SetResult(new { ok = true, action = "left_click", screen = new { x = sx, y = sy } });
                }
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// POST /click_tile { "x","y" }  — 瓦片坐标 → 真实鼠标左键点击该格（家具拿起/精确点击）
    /// 用 GlobalToLocal 把瓦片中心转成屏幕像素，再做真实左键点击（跟真人点画面一样）。
    /// </summary>
    private object HandleClickTile(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var tx = GetParamOr(p, "x", -1);
        var ty = GetParamOr(p, "y", -1);
        if (tx < 0 || ty < 0)
            return new { ok = false, error = "need x,y tile" };

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                // 瓦片中心 → 屏幕像素（含缩放/视口偏移）
                var local = Game1.GlobalToLocal(Game1.viewport, new Vector2(tx * 64 + 32, ty * 64 + 32));
                int sx = (int)local.X, sy = (int)local.Y;
                var win = Game1.game1.Window.ClientBounds;
                int ax = win.X + sx, ay = win.Y + sy;
                Game1.setMousePosition(sx, sy);
                // 确保游戏在前台（后台点击会落到别的窗口/被系统吞掉）
                try { SetForegroundWindow(Game1.game1.Window.Handle); } catch { }
                System.Threading.Thread.Sleep(100);
                SetCursorPos(ax, ay);
                System.Threading.Thread.Sleep(50);
                mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, UIntPtr.Zero);
                System.Threading.Thread.Sleep(30);
                mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, UIntPtr.Zero);
                tcs.SetResult(new { ok = true, tile = new { x = tx, y = ty },
                    screen = new { x = sx, y = sy }, absolute = new { x = ax, y = ay } });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// POST /drag { "x1","y1","x2","y2","hold"? }
    /// 鼠标拖拽：按住左键(x1,y1) → 拖动到(x2,y2) → 松开。
    /// 用于弹弓拉弓发射（按住→朝反方向拖动→松开）。
    /// </summary>
    private object HandleDrag(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var x1 = GetParamOr(p, "x1", -1);
        var y1 = GetParamOr(p, "y1", -1);
        var x2 = GetParamOr(p, "x2", -1);
        var y2 = GetParamOr(p, "y2", -1);
        var hold = GetParamOr(p, "hold", 300);

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                try { SetForegroundWindow(Game1.game1.Window.Handle); } catch { }
                var win = Game1.game1.Window.ClientBounds;
                int sx1 = x1 >= 0 ? x1 : win.X + win.Width / 2;
                int sy1 = y1 >= 0 ? y1 : win.Y + win.Height / 2;
                int sx2 = x2 >= 0 ? x2 : win.X + win.Width / 2;
                int sy2 = y2 >= 0 ? y2 : win.Y + win.Height / 2;
                SetCursorPos(sx1, sy1);
                mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, UIntPtr.Zero);
                System.Threading.Thread.Sleep(hold);  // 按住拉弓
                SetCursorPos(sx2, sy2);               // 拖动
                System.Threading.Thread.Sleep(60);
                mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, UIntPtr.Zero);
                tcs.SetResult(new { ok = true, action = "drag", from = new { x = sx1, y = sy1 }, to = new { x = sx2, y = sy2 } });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// 🎯 满包接鱼/领取 手动替换（恒 2026-08-23 治本）：原子、引用式、不走坐标。
    /// 拿领取侧第一个物品 → 换进背包指定格(或自动首个非工具/武器格) → 旧物即弃。
    /// POST /menu/claim_swap  { "replace": "<要替换的物品名，空=自动找>" }
    private object HandleClaimSwap(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var replace = GetParamOr(p, "replace", "");
        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                if (Game1.activeClickableMenu is not ItemGrabMenu igm)
                { tcs.SetResult(new { ok = false, error = "当前不是 ItemGrabMenu" }); return; }

                // 1) 领取侧第一个非空物品
                var grabInv = igm.ItemsToGrabMenu.actualInventory;
                Item? grab = null; int grabIdx = -1;
                for (int i = 0; i < grabInv.Count; i++)
                    if (grabInv[i] != null) { grab = grabInv[i]; grabIdx = i; break; }
                if (grab == null) { tcs.SetResult(new { ok = false, error = "领取侧没有物品" }); return; }
                string grabName = grab.DisplayName ?? grab.Name;

                // 2) 找背包格：replace=""(自动) 优先空槽（不丢物，恒 2026-08-23）；没空槽才找可替换物
                //    显式 replace 指定名则仍替换该物（AI 想清特定格）。
                int junkSlot = -1; string junkName = "";
                if (replace == "")
                {
                    for (int i = 0; i < Game1.player.Items.Count; i++)
                        if (Game1.player.Items[i] == null) { junkSlot = i; junkName = "(空槽)"; break; }
                }
                if (junkSlot < 0)
                {
                    for (int i = 0; i < Game1.player.Items.Count; i++)
                    {
                        var it = Game1.player.Items[i];
                        if (it == null) continue;
                        bool match = replace != ""
                            ? (it.Name.Equals(replace, StringComparison.OrdinalIgnoreCase)
                               || it.DisplayName.Equals(replace, StringComparison.OrdinalIgnoreCase)
                               || it.QualifiedItemId == replace)
                            : (it.Category > -98);   // 避开工具(-99)/武器(-98)
                        if (match) { junkSlot = i; junkName = it.DisplayName ?? it.Name; break; }
                    }
                }
                if (junkSlot < 0) { tcs.SetResult(new { ok = false, error = $"背包无可替换物[{(replace == "" ? "自动" : replace)}]" }); return; }

                // 3) 替换：领取物进背包格，旧物被覆盖即弃（不再引用）
                grabInv[grabIdx] = null;
                Game1.player.Items[junkSlot] = grab;

                tcs.SetResult(new { ok = true, claimed = grabName, replaced = junkName, slot = junkSlot });
            }
            catch (Exception ex) { tcs.SetResult(new { ok = false, error = ex.Message }); }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    private object HandleMenuClick(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var option = GetParamOr(p, "option", -1);
        var button = GetParamOr(p, "button", "");
        var clickX = GetParamOr(p, "x", -1);
        var clickY = GetParamOr(p, "y", -1);
        var item = GetParamOr(p, "item", "");   // 按物品名/ID 直点商店格子（免翻页）
        var right = GetParamOr(p, "right", false);  // 右键：商店买5/卖1个、背包拆堆叠取1个
        var quantity = GetParamOr(p, "quantity", 1);  // 批量：买 N 个 / 拆堆叠取 N 个
        var action = GetParamOr(p, "action", "");    // split=背包拆/ discard=丢弃 / claim=领取
        var slotIdx = GetParamOr(p, "slot", -1);     // 按背包槽位 index 直点（不依赖坐标，恒 2026-08-10）
        var real = GetParamOr(p, "real", false);     // real=true: 对话选项走真实 receiveLeftClick 响应（createQuestionDialogue 用，如跳舞邀请；跳过 event.answerDialogueQuestion）

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var menu = Game1.activeClickableMenu;
                if (menu == null)
                {
                    tcs.SetResult(new { ok = false, error = "No menu open" });
                    return;
                }

                if (option >= 0 && menu is DialogueBox db)
                {
                    var responseField = typeof(DialogueBox).GetField("responseCC",
                        System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic |
                        System.Reflection.BindingFlags.Instance);
                    var responseCCs = responseField?.GetValue(db) as List<ClickableComponent>;

                    // 🆕 2026-08-18 恒发现：节日/event 对话选项必须走 Event.answerDialogueQuestion(NPC, responseKey)——
                    //    receiveLeftClick→Dialogue.answerDialogue 对 event 对话返回 null → 直接 closeDialogue（选项不被认可）。
                    // ⚠️ 2026-08-19：real=true 时跳过——createQuestionDialogue（跳舞邀请/克林特菜单等）不走 event 问题，
                    //    必须走真实 receiveLeftClick 响应回调（proposal.response 才设得上）。
                    if (Game1.CurrentEvent != null && !real && responseCCs != null && option < responseCCs.Count)
                    {
                        var respField = typeof(DialogueBox).GetField("responses",
                            System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic |
                            System.Reflection.BindingFlags.Instance);
                        var respArr = respField?.GetValue(db) as IEnumerable<Response>;
                        var respList = respArr?.ToList();
                        string answerKey = (respList != null && option < respList.Count)
                            ? respList[option].responseKey : option.ToString();
                        var evt = Game1.CurrentEvent;
                        var npc = Game1.currentLocation?.isCharacterAtTile(Game1.player.GetGrabTile());
                        var eFlags = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public
                            | System.Reflection.BindingFlags.NonPublic;
                        var am = evt.GetType().GetMethod("answerDialogueQuestion", eFlags);
                        if (am != null)
                        {
                            try
                            {
                                am.Invoke(evt, new object?[] { npc, answerKey });
                                tcs.SetResult(new { ok = true, clicked = "response", option, key = answerKey, method = "event_answerDialogueQuestion" });
                                return;
                            }
                            catch (Exception ex)
                            {
                                tcs.SetResult(new { ok = false, error = $"event answer failed: {ex.Message}" });
                                return;
                            }
                        }
                    }

                    if (responseCCs != null && option < responseCCs.Count)
                    {
                        // ⚠️ 关键：receiveLeftClick 在 selectedResponse==-1 时直接 return（选中靠 hover 设置）。
                        // 必须先设 selectedResponse，点击才会触发 answerDialogue。
                        db.selectedResponse = option;
                        var rc = responseCCs[option];
                        db.receiveLeftClick(rc.bounds.Center.X, rc.bounds.Center.Y);
                        tcs.SetResult(new { ok = true, clicked = "response", option });
                    }
                    else
                    {
                        tcs.SetResult(new { ok = false, error = $"Response index {option} out of range" });
                    }
                    return;
                }

                // ⚠️ 按背包槽位 index 直点（不依赖工具算坐标——2026-08-10 恒：按背包格点最可靠）。
                // 用菜单的 InventoryMenu 组件真实坐标，菜单在哪都准（恒/锻造/AI 后台都行）。
                if (slotIdx >= 0)
                {
                    var bFlags = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic
                        | System.Reflection.BindingFlags.Instance;
                    // ⚠️ 扫描找 InventoryMenu 字段（ForgeMenu 等字段名不一定是 "inventory"，穷举最稳）。
                    // ⚠️ 2026-08-11 修商店误买：ShopMenu 的 forSaleButtons 和背包槽位坐标有重叠，
                    //    menu.receiveLeftClick(slotCenter) 会先命中商品按钮→误买。
                    //    改直接调 im.leftClick 操作玩家背包（拿/放），绕过商店的买/卖逻辑。
                    InventoryMenu? im = null;
                    foreach (var f in menu.GetType().GetFields(bFlags))
                    {
                        if (f.GetValue(menu) is InventoryMenu cand && cand.inventory != null) { im = cand; break; }
                    }
                    // 🎯 满包接鱼/领取：ItemGrabMenu 有两个 InventoryMenu（玩家背包 inventory + 领取侧 ItemsToGrabMenu）。
                    // 点 slot 要操作玩家背包（放/拿/把物品），上面扫描可能先抓到 ItemsToGrabMenu → 强指定玩家背包（恒 2026-08-23 治本）。
                    if (menu is ItemGrabMenu igmSlot && !(igmSlot.reverseGrab || igmSlot.behaviorFunction != null) && im != igmSlot.inventory)
                    {
                        im = igmSlot.inventory;
                    }
                    // ⚠️ GameMenu 的背包在 InventoryPage（私有嵌套类）里，从当前页找
                    if (im == null && menu is GameMenu gm2 && gm2.currentTab >= 0 && gm2.currentTab < gm2.pages.Count)
                    {
                        var page2 = gm2.pages[gm2.currentTab];
                        foreach (var f in page2.GetType().GetFields(bFlags))
                        {
                            if (f.GetValue(page2) is InventoryMenu cand2 && cand2.inventory != null) { im = cand2; break; }
                        }
                    }
                    // 🎁 送礼菜单（冬星节神秘礼物等：ItemGrabMenu + reverseGrab/behaviorFunction）：
                    // 反编译 Event.cs:12873 + ItemGrabMenu.receiveLeftClick:885 —— 真人单击 =
                    //   base.receiveLeftClick（MenuWithInventory 拿起物品进 heldItem）+ reverseGrab 分支调
                    //   behaviorFunction(=Event.chooseSecretSantaGift) → 送出礼物+退出菜单。
                    // ⚠️ 不能只调 im.leftClick（只拿起进光标，物品"消失"在 cursor，behaviorFunction 不触发）。
                    //    必须走 menu.receiveLeftClick，且用底部 base.inventory（真人在点的那个）槽位坐标。
                    if (menu is ItemGrabMenu igmGift && (igmGift.reverseGrab || igmGift.behaviorFunction != null) && !right)
                    {
                        var gInv = igmGift.inventory;   // MenuWithInventory.inventory = 底部玩家背包（被点击侧）
                        if (gInv != null && slotIdx >= 0 && slotIdx < gInv.inventory.Count)
                        {
                            var gcc = gInv.inventory[slotIdx];
                            if (gcc != null)
                            {
                                menu.receiveLeftClick(gcc.bounds.Center.X, gcc.bounds.Center.Y);
                                tcs.SetResult(new { ok = true, clicked = "gift_send", slot = slotIdx,
                                    x = gcc.bounds.Center.X, y = gcc.bounds.Center.Y, menu = menu.GetType().Name });
                                return;
                            }
                        }
                        tcs.SetResult(new { ok = false, error = $"送礼菜单槽位 {slotIdx} 不可用" });
                        return;
                    }
                    if (im != null && slotIdx >= 0 && slotIdx < im.inventory.Count)
                    {
                        var scc = im.inventory[slotIdx];
                        if (scc != null)
                        {
                            if (right)
                            {
                                // 右键：拆堆叠/取1个——走菜单 receiveRightClick（菜单内处理）
                                menu.receiveRightClick(scc.bounds.Center.X, scc.bounds.Center.Y);
                            }
                            else
                            {
                                // ⚠️ 左键拿/放：直接 InventoryMenu.leftClick，不经过菜单的买/卖按钮
                                var cx = scc.bounds.Center.X;
                                var cy = scc.bounds.Center.Y;
                                // 光标物品用反射读（ShopMenu 的 ISalable heldItem 是隐藏字段）
                                var held = GetMenuHeldItem(menu);
                                var picked = im.leftClick(cx, cy, held);
                                // 反射更新光标
                                SetMenuHeldItem(menu, picked);
                            }
                            tcs.SetResult(new { ok = true, clicked = "slot", slot = slotIdx,
                                x = scc.bounds.Center.X, y = scc.bounds.Center.Y });
                            return;
                        }
                    }
                    tcs.SetResult(new { ok = false, error = $"slot {slotIdx} 不在背包" });
                    return;
                }

                // 献祭捐赠（2026-08-16）：JunimoNoteMenu 按物品名直接捐（不靠坐标）。
                // 反编译 tryToDepositThisItem(Item, slot, "LooseSprites\JunimoNote", menu)：找到能接受的
                // ingredient slot 直接存入；checkIfBundleIsComplete 更新完成状态。
                if (item != "" && menu.GetType().Name == "JunimoNoteMenu")
                {
                    var jFlags = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic
                        | System.Reflection.BindingFlags.Instance;
                    var jType = menu.GetType();
                    var curBundle = jType.GetField("currentPageBundle", jFlags)?.GetValue(menu);
                    if (curBundle == null)
                    {
                        tcs.SetResult(new { ok = false, error = "当前不在 bundle 具体页——先 menu_click 点 bundleBounds 进页，再 menu_click(item=物品名) 捐赠" });
                        return;
                    }
                    var bt = curBundle.GetType();
                    bool complete = false;
                    try { complete = Convert.ToBoolean(bt.GetField("complete", jFlags)?.GetValue(curBundle) ?? false); } catch { }
                    if (complete)
                    {
                        tcs.SetResult(new { ok = false, error = "这个 bundle 已完成，不用再捐" });
                        return;
                    }
                    var found = Game1.player.Items.FirstOrDefault(it => it != null &&
                        (it.Name.Equals(item, StringComparison.OrdinalIgnoreCase)
                         || it.DisplayName.Equals(item, StringComparison.OrdinalIgnoreCase)
                         || it.QualifiedItemId == item));
                    if (found == null)
                    {
                        tcs.SetResult(new { ok = false, error = $"背包里没有「{item}」" });
                        return;
                    }
                    var slots = jType.GetField("ingredientSlots", jFlags)?.GetValue(menu) as List<ClickableTextureComponent>;
                    if (slots != null)
                    {
                        for (int i = 0; i < slots.Count; i++)
                        {
                            bool canAccept = false;
                            try
                            {
                                // ⚠️ canAcceptThisItem 有 2 参/3 参两个重载，GetMethod 可能返回 3 参的导致 Invoke 参数数错误
                                //    → 显式取 2 参重载 (Item, ClickableTextureComponent)
                                var acc = bt.GetMethod("canAcceptThisItem",
                                    new Type[] { typeof(StardewValley.Item), typeof(ClickableTextureComponent) });
                                if (acc != null) canAccept = Convert.ToBoolean(acc.Invoke(curBundle, new object[] { found, slots[i] }) ?? false);
                            }
                            catch { }
                            if (canAccept)
                            {
                                // 找物品在背包的槽位
                                var playerItems = Game1.player.Items;
                                int slotIdx = -1;
                                for (int z = 0; z < playerItems.Count; z++)
                                    if (playerItems[z] == found) { slotIdx = z; break; }
                                int fStackBefore = found.Stack;
                                bool slotItemBefore = slots[i].item != null;
                                string? excMsg = null;
                                // 模拟"拿起"：设 heldItem=物品，并从背包移除（镜像手动流程：先从背包拿起再放槽）
                                //  ⚠️ 2026-08-16 复盘：只设 heldItem 不拿背包，ConsumeStack 不会反映到背包数 → 消耗不生效
                                if (slotIdx >= 0)
                                {
                                    if (found.Stack <= 1)
                                    {
                                        playerItems[slotIdx] = null;   // 整叠拿走
                                        jType.GetField("heldItem", jFlags)?.SetValue(menu, found);
                                    }
                                    else
                                    {
                                        // 堆叠物品：拿 1 个放光标，其余留背包
                                        var one = found.getOne();
                                        if (one != null)
                                        {
                                            found.Stack -= 1;
                                            jType.GetField("heldItem", jFlags)?.SetValue(menu, one);
                                        }
                                    }
                                }
                                else
                                {
                                    jType.GetField("heldItem", jFlags)?.SetValue(menu, found);
                                }
                                // ⚠️ 附魔/商店经验（CLAUDE.md 7939-7940）：槽位放置读实际鼠标位置(Game1.getMouseX/Y)，
                                //    必须 setMousePosition 对齐（去掉后放置失败）。捐赠同款。
                                int slotCX = slots[i].bounds.Center.X, slotCY = slots[i].bounds.Center.Y;
                                try { Game1.setMousePosition(slotCX, slotCY); } catch { }
                                try { menu.receiveLeftClick(slotCX, slotCY); }
                                catch (Exception ex) { excMsg = ex.Message; }
                                var afterHeld = jType.GetField("heldItem", jFlags)?.GetValue(menu) as Item;
                                bool slotItemAfter = slots[i].item != null;
                                bool ingredientCompleted = false;
                                try
                                {
                                    if (bt.GetField("ingredients", jFlags)?.GetValue(curBundle) is System.Collections.IEnumerable ings)
                                    {
                                        int c = 0;
                                        foreach (var ing in ings)
                                        {
                                            if (c == i)
                                            {
                                                try { ingredientCompleted = Convert.ToBoolean(ing.GetType().GetField("completed", jFlags)?.GetValue(ing) ?? false); } catch { }
                                                break;
                                            }
                                            c++;
                                        }
                                    }
                                }
                                catch { }
                                tcs.SetResult(new { ok = true, donated = item, slot = i, leftover = afterHeld?.DisplayName,
                                    foundStackBefore = fStackBefore, foundStackAfter = found.Stack,
                                    slotItemBefore, slotItemAfter, ingredientCompleted, exc = excMsg });
                                return;
                            }
                        }
                    }
                    tcs.SetResult(new { ok = false, error = $"「{item}」不匹配当前 bundle 的槽位（先 read_menu 看要什么）" });
                    return;
                }

                // 按物品名/ID 直接点商店格子（免翻页）：跳到目标物品所在页再点它的按钮。
                // 买/取一件东西不用翻 50 页，一条调用搞定。皮埃尔/衣柜/鱼缸/马龙全适用。
                // quantity=N 时内部循环买 N 个（每个买完自动放包），AI 一句"要 30 个"一次调用。
                if (item != "" && menu is ShopMenu shop)
                {
                    int qty = Math.Max(1, Math.Min(quantity, 999));
                    int bought = 0;
                    for (int q = 0; q < qty; q++)
                    {
                        var match = shop.forSale.FirstOrDefault(it =>
                            it.QualifiedItemId == item
                            || it.Name.Equals(item, StringComparison.OrdinalIgnoreCase)
                            || it.DisplayName.Equals(item, StringComparison.OrdinalIgnoreCase));
                        if (match == null) break;
                        int idx = shop.forSale.IndexOf(match);
                        int maxIdx = Math.Max(0, shop.forSale.Count - 4);
                        shop.currentItemIndex = Math.Max(0, Math.Min(idx, maxIdx));
                        int btnIdx = idx - shop.currentItemIndex;
                        if (btnIdx < 0 || btnIdx >= shop.forSaleButtons.Count) break;
                        var btn = shop.forSaleButtons[btnIdx];
                        shop.receiveLeftClick(btn.bounds.Center.X, btn.bounds.Center.Y);
                        // ⚠️ 普通商店购买后物品在 heldItem（光标），真人还要点背包格放下；
                        // StorageFurniture（衣柜/鱼缸）会自动放。这里补上：heldItem 直接进背包。
                        if (shop.heldItem is Item held && Game1.player.addItemToInventoryBool(held))
                        {
                            shop.heldItem = null;
                            bought++;
                        }
                        else if (shop.heldItem is Item held2)
                        {
                            // 背包满或该物品无法直接进包：丢到玩家身边，不卡循环
                            shop.heldItem = null;
                            Game1.createItemDebris(held2, Game1.player.getStandingPosition(), 0);
                            bought++;
                        }
                    }
                    tcs.SetResult(new { ok = true, clicked = "shop_item", item = item, quantity = bought });
                    return;
                }

                // 🎒 自适应：背包菜单按物品名找槽操作（拆分/丢弃），奖励/箱子菜单领取指定物品
                if (item != "")
                {
                    var bFlags = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic
                        | System.Reflection.BindingFlags.Instance;

                    // GameMenu 背包页：找到物品所在槽
                    if (menu is GameMenu gm)
                    {
                        IClickableMenu? page = gm.currentTab >= 0 && gm.currentTab < gm.pages.Count
                            ? gm.pages[gm.currentTab] : null;
                        List<ClickableComponent>? invSlots = null;
                        if (page != null)
                        {
                            var invField = page.GetType().GetField("inventory", bFlags);
                            if (invField?.GetValue(page) is InventoryMenu im)
                                invSlots = im.inventory;
                        }
                        if (invSlots != null)
                        {
                            int slotIdx = -1;
                            for (int i = 0; i < Game1.player.Items.Count && i < invSlots.Count; i++)
                            {
                                var it = Game1.player.Items[i];
                                if (it != null && (it.Name.Equals(item, StringComparison.OrdinalIgnoreCase)
                                    || it.DisplayName.Equals(item, StringComparison.OrdinalIgnoreCase)
                                    || it.QualifiedItemId == item))
                                { slotIdx = i; break; }
                            }
                            if (slotIdx < 0)
                            {
                                tcs.SetResult(new { ok = false, error = $"背包里没有「{item}」" });
                                return;
                            }
                            var cc = invSlots[slotIdx];
                            if (action == "discard")
                            {
                                // 拿起 → 点垃圾桶删除
                                gm.receiveLeftClick(cc.bounds.Center.X, cc.bounds.Center.Y);
                                var trashField = page.GetType().GetField("trashCan", bFlags);
                                if (trashField?.GetValue(page) is ClickableTextureComponent trashCan && trashCan.visible)
                                {
                                    gm.receiveLeftClick(trashCan.bounds.Center.X, trashCan.bounds.Center.Y);
                                    tcs.SetResult(new { ok = true, clicked = "bag_discard", item });
                                }
                                else
                                {
                                    tcs.SetResult(new { ok = true, clicked = "bag_picked_up", item });
                                }
                            }
                            else
                            {
                                // 拆分：右键取 quantity 个
                                int rq = Math.Max(1, Math.Min(quantity, 999));
                                for (int q = 0; q < rq; q++)
                                    gm.receiveRightClick(cc.bounds.Center.X, cc.bounds.Center.Y);
                                tcs.SetResult(new { ok = true, clicked = "bag_split", item, quantity = rq });
                            }
                            return;
                        }
                    }
                    else if (menu is ItemGrabMenu igm)
                    {
                        // 🎁 送礼菜单（冬星节神秘礼物等：reverseGrab/behaviorFunction）：
                        // 真人单击=base 拿起物品进 heldItem + 调 behaviorFunction(=chooseSecretSantaGift) 送出。
                        // 必须 menu.receiveLeftClick（不能只 leftClick 拿起），且用底部 base.inventory 槽位坐标。
                        if (igm.reverseGrab || igm.behaviorFunction != null)
                        {
                            var gInv = igm.inventory;   // 底部玩家背包（被点击侧）
                            if (gInv != null)
                            {
                                for (int i = 0; i < Game1.player.Items.Count && i < gInv.inventory.Count; i++)
                                {
                                    var pItem = Game1.player.Items[i];
                                    if (pItem == null) continue;
                                    if (pItem.Name.Equals(item, StringComparison.OrdinalIgnoreCase)
                                        || pItem.DisplayName.Equals(item, StringComparison.OrdinalIgnoreCase)
                                        || pItem.QualifiedItemId == item)
                                    {
                                        var gcc = gInv.inventory[i];
                                        if (gcc != null)
                                        {
                                            igm.receiveLeftClick(gcc.bounds.Center.X, gcc.bounds.Center.Y);
                                            tcs.SetResult(new { ok = true, clicked = "gift_send", item, slot = i });
                                            return;
                                        }
                                    }
                                }
                                tcs.SetResult(new { ok = false, error = $"送礼菜单背包里没有「{item}」" });
                                return;
                            }
                        }
                        // 🎁 领取：找菜单里该物品的格子点击（公会奖励/箱子）——读 actualInventory 真物品，
                        // 用 inventory[i] 槽位坐标（恒 2026-08-23 治本：ItemsToGrabMenu.inventory 组件 stale，
                        // 真物品在 actualInventory，读错才"领取菜单里没有"；满包接鱼/弃箱手动替换全靠它）。
                        var grabInv = igm.ItemsToGrabMenu.actualInventory;
                        var grabSlots = igm.ItemsToGrabMenu.inventory;
                        for (int i = 0; i < grabInv.Count && i < grabSlots.Count; i++)
                        {
                            var cit = grabInv[i];
                            var cc = grabSlots[i];
                            if (cit == null || cc == null) continue;
                            if (cit.Name.Equals(item, StringComparison.OrdinalIgnoreCase)
                                || cit.DisplayName.Equals(item, StringComparison.OrdinalIgnoreCase)
                                || cit.QualifiedItemId == item)
                            {
                                igm.receiveLeftClick(cc.bounds.Center.X, cc.bounds.Center.Y);
                                tcs.SetResult(new { ok = true, clicked = "claim", item, slot = i });
                                return;
                            }
                        }
                        tcs.SetResult(new { ok = false, error = $"领取菜单里没有「{item}」" });
                        return;
                    }
                }

                if (button != "")
                {
                    var field = menu.GetType().GetField(button == "ok" ? "okButton" :
                                                        button == "cancel" ? "cancelButton" :
                                                        button == "back" ? "backButton" :
                                                        button == "forward" ? "forwardButton" :
                                                        button == "close" ? "upperRightCloseButton" :
                                                        button,
                        System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic |
                        System.Reflection.BindingFlags.Instance);
                    var comp = field?.GetValue(menu) as ClickableComponent;
                    if (comp != null)
                    {
                        menu.receiveLeftClick(comp.bounds.Center.X, comp.bounds.Center.Y);
                        tcs.SetResult(new { ok = true, clicked = "button", button });
                    }
                    else
                    {
                        tcs.SetResult(new { ok = false, error = $"Button '{button}' not found" });
                    }
                    return;
                }

                if (clickX >= 0 && clickY >= 0)
                {
                    // ⚠️ 2026-08-10 恢复：ForgeMenu 的槽位放置读实际鼠标位置(Game1.getMouseX/Y)，
                    //   必须 setMousePosition 对齐（去掉后槽位放置失败，恒确认）。
                    //   代价是挪 OS 光标——AI 前台操作时不干扰恒（恒让前台），后台操作才可见。
                    Game1.setMousePosition(clickX, clickY);
                    if (right)
                    {
                        // 右键批量：拆堆叠取 N 个（每个右键取 1）。quantity 上限 999。
                        int rq = Math.Max(1, Math.Min(quantity, 999));
                        for (int q = 0; q < rq; q++)
                            menu.receiveRightClick(clickX, clickY);
                        tcs.SetResult(new { ok = true, clicked = "position_right", x = clickX, y = clickY, quantity = rq });
                    }
                    else
                    {
                        menu.receiveLeftClick(clickX, clickY);
                        tcs.SetResult(new { ok = true, clicked = "position", x = clickX, y = clickY });
                    }
                    return;
                }

                menu.receiveLeftClick(
                    menu.xPositionOnScreen + menu.width / 2,
                    menu.yPositionOnScreen + menu.height / 2);
                tcs.SetResult(new { ok = true, clicked = "center" });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>反射读当前菜单的光标物品（heldItem）。heldItem 声明位置不统一：
    /// MenuWithInventory(ForgeMenu等) 是 Item 字段；ShopMenu 是 `new ISalable` 隐藏字段。反射最稳。</summary>
    private static Item? GetMenuHeldItem(IClickableMenu menu)
    {
        try
        {
            var f = menu.GetType().GetField("heldItem",
                System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic |
                System.Reflection.BindingFlags.Instance);
            return f?.GetValue(menu) as Item;
        }
        catch { return null; }
    }

    /// <summary>反射写当前菜单的光标物品（ShopMenu 的 ISalable 字段用 Item 也能赋，Item 实现 ISalable）。</summary>
    private static void SetMenuHeldItem(IClickableMenu menu, Item? item)
    {
        try
        {
            var f = menu.GetType().GetField("heldItem",
                System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic |
                System.Reflection.BindingFlags.Instance);
            if (f != null) f.SetValue(menu, item);
        }
        catch { }
    }

    /// <summary>
    /// POST /menu_close — 强制关闭当前菜单（清光标 + 退出）。
    /// 2026-08-11：解决"光标有物品时商店 readyToClose()=false 关不掉"的死结。
    /// 纯 C# 直接调用：heldItem 先 CollectOrDrop 放回背包（放不下就掉地上），再 exitThisMenu() 强关。
    /// 不点坐标、不抢焦点。清残局一条命令搞定。
    /// </summary>
    /// <summary>
    /// POST /character_customize  { "name"?, "farmname"?, "favorite"? }
    /// 处理 CharacterCustomization 捏人弹窗（新 farmhand 加入时的起名/喜好/确认）。
    /// ⚠️ 2026-08-15 恒：不用 /key/模拟按键、不抢前台、不 setMousePosition——
    /// 直接反射写菜单的 TextBox.Text + 同步 Game1.player 字段（canLeaveMenu 读 player 字段，OK 收尾从 box 重读）。
    /// 只传要改的字段；全部省略=只读当前状态 + canLeaveMenu。确认请调 /menu/click button=ok。
    /// </summary>
    private object HandleCharacterCustomize(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var cc = Game1.activeClickableMenu as StardewValley.Menus.CharacterCustomization;
                var bFlags = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic
                    | System.Reflection.BindingFlags.Instance;

                string name = GetParamOr(p, "name", "");
                string farmname = GetParamOr(p, "farmname", "");
                string favorite = GetParamOr(p, "favorite", "");

                // 捏人弹窗开着 → 同步写文本框（OK 收尾从 box 重读）；没开 → 直接改 player 字段（创建后可补救喜欢的东西等）。
                string curName = "", curFarm = "", curFav = "";
                if (cc != null)
                {
                    TextBox? GetBox(string f) => cc.GetType().GetField(f, bFlags)?.GetValue(cc) as TextBox;
                    var nameBox = GetBox("nameBox");
                    var farmnameBox = GetBox("farmnameBox");
                    var favThingBox = GetBox("favThingBox");
                    if (name != "" && nameBox != null) { nameBox.Text = name; }
                    if (farmname != "" && farmnameBox != null) { farmnameBox.Text = farmname; }
                    if (favorite != "" && favThingBox != null) { favThingBox.Text = favorite; }
                    curName = nameBox?.Text ?? "";
                    curFarm = farmnameBox?.Text ?? "";
                    curFav = favThingBox?.Text ?? "";
                }

                // 同步 Game1.player 字段（canLeaveMenu 读这个；没开菜单时也生效，用来补救/改身份）
                if (name != "") { Game1.player.Name = name; Game1.player.displayName = name; }
                if (favorite != "") { Game1.player.favoriteThing.Value = favorite; }
                if (farmname != "") { Game1.player.farmName.Value = farmname; }

                // 🏡 农场名不归 AI 填：AI 后加入的是已有农场，从房主(MasterPlayer)继承。
                // canLeaveMenu 读 Game1.player.farmName——空就自动补，AI 不用知道农场叫啥。
                if (Game1.player.farmName.Length == 0)
                {
                    var masterFarm = Game1.MasterPlayer?.farmName?.Value;
                    if (!string.IsNullOrEmpty(masterFarm))
                        Game1.player.farmName.Value = masterFarm;
                }

                bool canLeave = cc != null && cc.canLeaveMenu();

                string missing = "";
                if (Game1.player.Name.Length == 0) missing += "名字 ";
                if (Game1.player.favoriteThing.Length == 0) missing += "喜欢的东西";

                tcs.SetResult(new
                {
                    ok = true,
                    menuOpen = cc != null,
                    name = curName != "" ? curName : Game1.player.Name,
                    farmname = curFarm != "" ? curFarm : Game1.player.farmName.Value,
                    favorite = curFav != "" ? curFav : Game1.player.favoriteThing.Value,
                    playerName = Game1.player.Name,
                    canLeaveMenu = canLeave,
                    hint = canLeave
                        ? "✅ 满足确认条件，用 /menu/click button=ok 确认创建角色"
                        : (cc != null
                            ? "⚠️ 还差 " + missing + "——用 /character_customize {name, favorite} 填（农场名自动继承房主，不用传）"
                            : "ℹ️ 捏人弹窗已关；name/favorite 已直接改到角色字段（喜欢的东西创建后也能改）")
                });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// POST /color_pick  { "hue"?, "sat"?, "val"?, "hex"? }
    /// 颜色换算：HSV↔RGB，跟游戏颜色条(ColorPicker)完全一致（2026-08-15 恒：AI 想精确调色用）。
    /// - 给 hue/sat/val（都是 **0-100 整数**，对应游戏三根滑块——点击=(int)(x/宽*100)，切成 ~100 份）→ 返回 rgb + hex。
    ///   内部与游戏一致：hueDeg=hue/100*360，调 ColorPicker.HsvToRgb(hueDeg, sat/100, val/100)。
    /// - 给 hex → 返回 rgb + hsv（连续值 + 滑块最近整数档），方便按"色相/饱和/明度"口头调色、对齐游戏档位。
    /// </summary>
    private object HandleColorPick(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var hue = GetParamOr(p, "hue", -1.0);
        var sat = GetParamOr(p, "sat", -1.0);
        var val = GetParamOr(p, "val", -1.0);
        var hex = GetParamOr(p, "hex", "");

        try
        {
            if (hue >= 0 && sat >= 0 && val >= 0)
            {
                double hueDeg = hue / 100.0 * 360.0;
                var color = StardewValley.Menus.ColorPicker.HsvToRgb(hueDeg, sat / 100.0, val / 100.0);
                return new
                {
                    ok = true,
                    mode = "hsv",
                    slider = new { hue = (int)hue, sat = (int)sat, val = (int)val },
                    hueDeg = Math.Round(hueDeg, 1),
                    rgb = new[] { color.R, color.G, color.B },
                    hex = ColorToHex(color)
                };
            }
            if (!string.IsNullOrWhiteSpace(hex))
            {
                var c = ParseColor(hex);
                if (c.HasValue)
                {
                    RgbToHsv(c.Value, out double h, out double s, out double v);
                    return new
                    {
                        ok = true,
                        mode = "rgb",
                        rgb = new[] { c.Value.R, c.Value.G, c.Value.B },
                        hsv = new
                        {
                            hueDeg = Math.Round(h, 1),
                            sat = Math.Round(s * 100, 1),
                            val = Math.Round(v * 100, 1),
                            slider = new
                            {
                                hue = (int)Math.Round(h / 3.6),
                                sat = (int)Math.Round(s * 100),
                                val = (int)Math.Round(v * 100)
                            }
                        }
                    };
                }
                return new { ok = false, error = $"hex 解析失败: {hex}" };
            }
            return new { ok = false, error = "要传 hue/sat/val（0-100 滑块整数，HSV→RGB）或 hex（RGB→HSV）" };
        }
        catch (Exception ex)
        {
            return new { ok = false, error = ex.Message };
        }
    }

    /// <summary>Color → "RRGGBB"。appearance_info/color_pick 回报用（Color.ToString 是 MonoGame 的 {R:..} 格式，不是 hex）。</summary>
    private static string ColorToHex(Color c) => $"{c.R:X2}{c.G:X2}{c.B:X2}";

    /// <summary>标准 RGB→HSV（游戏只有 HsvToRgb，反向补一个，方便 AI 用色相/饱和/明度口头调色）。</summary>
    private static void RgbToHsv(Color c, out double h, out double s, out double v)
    {
        double r = c.R / 255.0, g = c.G / 255.0, b = c.B / 255.0;
        double max = Math.Max(r, Math.Max(g, b));
        double min = Math.Min(r, Math.Min(g, b));
        double d = max - min;
        v = max;
        s = max == 0 ? 0 : d / max;
        if (d == 0)
        {
            h = 0;
        }
        else if (max == r)
        {
            h = 60 * (((g - b) / d) % 6);
        }
        else if (max == g)
        {
            h = 60 * ((b - r) / d + 2);
        }
        else
        {
            h = 60 * ((r - g) / d + 4);
        }
        if (h < 0) h += 360;
    }

    private object HandleMenuClose()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var menu = Game1.activeClickableMenu;
                if (menu == null)
                {
                    tcs.SetResult(new { ok = true, closed = false, reason = "no menu" });
                    return;
                }
                var menuType = menu.GetType().Name;
                string? dropped = null;
                int cleared = 0;

                // 1) 清光标。⚠️ heldItem 声明位置不统一（MenuWithInventory=Item / ShopMenu=ISalable），
                //    用反射读，拿到 Item 就 CollectOrDrop 放回背包。
                try
                {
                    var held = GetMenuHeldItem(menu);
                    if (held != null)
                    {
                        dropped = held.DisplayName ?? held.Name;
                        if (Utility.CollectOrDrop(held))
                            Game1.playSound("stoneStep");
                        else
                            Game1.playSound("throwDownITem");
                        SetMenuHeldItem(menu, null);
                        cleared++;
                    }
                }
                catch { }

                // 2) 强制退出菜单（exitThisMenu 不检查 readyToClose）
                try { menu.exitThisMenu(playSound: true); } catch { }

                tcs.SetResult(new { ok = true, closed = true, menu = menuType, clearedCursor = cleared, dropped });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>按 QualifiedItemId / Name / DisplayName 在玩家背包找物品。</summary>
    private static Item? FindItemByNameOrId(string s)
    {
        if (string.IsNullOrEmpty(s)) return null;
        foreach (var i in Game1.player.Items)
        {
            if (i == null) continue;
            if (i.QualifiedItemId == s || i.Name == s || i.DisplayName == s) return i;
        }
        return null;
    }

    /// <summary>从背包取 1 个物品返回（堆叠减1取副本；单件整个移除）。</summary>
    private static Item TakeOne(Item item)
    {
        var list = Game1.player.Items;
        int idx = list.IndexOf(item);
        if (idx < 0) return item;
        if (item.Stack > 1)
        {
            item.Stack--;
            return item.getOne();
        }
        list[idx] = null;
        return item;
    }

    /// <summary>
    /// POST /forge_set { left: itemId/名, right: itemId/名 } — 直接设置已打开的锻造台槽位。
    /// 2026-08-11：后台 /state 背包读取陈旧导致 slot 点击打错格（拿起 Cinder Shard 整组），
    /// 菜单点击放料不可靠 → 改主线程直接设槽（从背包取1个放槽），不碰鼠标。
    /// 之后 MCP 端 menu_click 点锻造按钮（位置可靠）+ 领取结果。
    /// </summary>
    private object HandleForgeSet(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var left = GetParamOr(p, "left", "");
        var right = GetParamOr(p, "right", "");

        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                if (Game1.activeClickableMenu is not StardewValley.Menus.ForgeMenu forge)
                {
                    tcs.SetResult(new { ok = false, error = "锻造台没开" });
                    return;
                }
                // 清左右槽残留（放回背包）
                foreach (var spot in new[] { forge.leftIngredientSpot, forge.rightIngredientSpot })
                {
                    if (spot?.item != null)
                    {
                        var it = spot.item;
                        if (!Game1.player.addItemToInventoryBool(it))
                            Game1.createItemDebris(it, Game1.player.getStandingPosition(), Game1.player.FacingDirection);
                        spot.item = null;
                    }
                }
                // 放左槽
                if (left != "")
                {
                    var li = FindItemByNameOrId(left);
                    if (li == null)
                    {
                        tcs.SetResult(new { ok = false, error = $"背包没有「{left}」" });
                        return;
                    }
                    forge.leftIngredientSpot.item = TakeOne(li);
                }
                // 放右槽（失败回滚左槽）
                if (right != "")
                {
                    var ri = FindItemByNameOrId(right);
                    if (ri == null)
                    {
                        if (forge.leftIngredientSpot.item != null)
                        {
                            Game1.player.addItemToInventoryBool(forge.leftIngredientSpot.item);
                            forge.leftIngredientSpot.item = null;
                        }
                        tcs.SetResult(new { ok = false, error = $"背包没有「{right}」" });
                        return;
                    }
                    forge.rightIngredientSpot.item = TakeOne(ri);
                }
                // 刷新验证（protected，反射调）
                try
                {
                    var v = typeof(StardewValley.Menus.ForgeMenu).GetMethod("_ValidateCraft",
                        System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
                    v?.Invoke(forge, null);
                }
                catch { }
                tcs.SetResult(new { ok = true,
                    left = forge.leftIngredientSpot.item?.Name,
                    right = forge.rightIngredientSpot.item?.Name });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    private object HandleDumpTile(HttpListenerContext ctx)
    {
        var qs = ctx.Request.QueryString;
        if (!int.TryParse(qs["x"], out var x) || !int.TryParse(qs["y"], out var y))
            return new { ok = false, error = "need x,y" };

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var loc = Game1.player.currentLocation;
                var tv = new Vector2(x, y);
                var result = new Dictionary<string, object?>();
                if (loc.objects.TryGetValue(tv, out var obj))
                {
                    result["object"] = new
                    {
                        itemId = obj.itemId?.Value,
                        qualifiedId = obj.QualifiedItemId,
                        parentSheetIndex = obj.ParentSheetIndex,
                        name = SafeDisplayName(obj),
                        typeName = obj.GetType().Name
                    };
                }
                result["terrain"] = loc.terrainFeatures.TryGetValue(tv, out var tf) ? tf.GetType().Name : null;
                // 🫚 crop/forageCrop（2026-08-17）：姜=forageCrop 类型"2"，锄地出（hitWithHoe）
                if (tf is HoeDirt _hd && _hd.crop != null)
                {
                    result["crop"] = _hd.crop.indexOfHarvest.Value;
                    result["cropPhase"] = _hd.crop.currentPhase.Value;
                    result["harvestable"] = _hd.readyForHarvest();
                    if (_hd.crop.forageCrop.Value)
                        result["forageCrop"] = _hd.crop.whichForageCrop.Value;   // "2"=姜
                }
                // 🌿 树苔藓（2026-08-17）：treeType 10/11/12=绿雨树，hasMoss=长苔藓可收 Moss
                if (tf is Tree _tree)
                {
                    result["treeType"] = _tree.treeType.Value;
                    result["moss"] = _tree.hasMoss.Value;
                    result["greenRainTree"] = _tree.isTemporaryGreenRainTree.Value;
                }
                // 🍓 灌木丛（2026-08-17）：Bush 在 largeTerrainFeatures 不在 terrainFeatures
                var ltf = loc.largeTerrainFeatures.FirstOrDefault(l => l.Tile == tv);
                if (ltf != null)
                {
                    result["largeTerrain"] = ltf.GetType().Name;
                    if (ltf is Bush bush)
                    {
                        result["largeTerrain"] = "Bush";
                        result["tileSheetOffset"] = bush.tileSheetOffset.Value;   // 1=结果(有莓果)可摇
                        result["bushBloom"] = bush.tileSheetOffset.Value == 1;
                    }
                }
                result["passable"] = loc.isTilePassable(tv);
                result["isWater"] = loc.isWaterTile(x, y);   // 🦀 蟹笼部署找水用（2026-08-16）
                // 🦀 蟹笼内部状态（2026-08-16 诊断饵挂不上）：bait/readyForHarvest/owner
                if (loc.objects.TryGetValue(tv, out var obj2) && obj2 is StardewValley.Objects.CrabPot cp)
                {
                    result["crabPot"] = new
                    {
                        bait = cp.bait?.Value?.DisplayName,
                        baitId = cp.bait?.Value?.QualifiedItemId,
                        readyForHarvest = cp.readyForHarvest.Value,
                        owner = cp.owner.Value,
                        tile = new { x = cp.TileLocation.X, y = cp.TileLocation.Y },
                    };
                }
                tcs.SetResult(new { ok = true, location = loc.Name, x, y, tile = result });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// GET /mine_rock
    /// 矮人商店堵路石状态（cross-map 读，2026-08-23 恒）。
    /// SDV 矿洞矮人商店前固定一块可破坏石头（(BC)78 圆石，每个档同在 Mine(27,8)，炸掉后不再生）。
    /// 未炸（石头还在 loc.objects）= blocked=true → 门禁拦（AI 去不了矮人商店，需先炸开）；
    /// 炸掉（object 消失）= blocked=false → 放行。⚠️ 读指定地图（非玩家当前层），跨层可靠。
    /// </summary>
    private object HandleMineRock()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var mine = Game1.getLocationFromName("Mine");
                var tv = new Vector2(27, 8);
                bool blocked = false;
                string rockId = null, rockName = null;
                if (mine?.objects != null && mine.objects.TryGetValue(tv, out var obj))
                {
                    rockId = obj.QualifiedItemId;
                    rockName = obj.Name;
                    // 圆石(BC)78 不可踏（isPassable=false），挡住去矮人的路
                    blocked = obj.QualifiedItemId == "(BC)78" || (obj.ParentSheetIndex == 78 && !obj.isPassable());
                }
                tcs.SetResult(new { ok = true, location = "Mine", x = 27, y = 8, blocked, rockId, rockName });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// POST /dig_spot {x, y}  挖蚯蚓格子/姜（2026-08-17 恒+克劳德）
    /// 蚯蚓格子 = object (O)590 "Artifact Spot"，锄头挖 → GameLocation.digUpArtifactSpot 出古物/矿物/种子。
    /// 姜岛的姜同款锄地逻辑（锄草地随机出，无固定实体——AI 直接 farm ops=till 锄地即可）。
    /// 掉落物有吸附，玩家在附近自动进包；挖完蚯蚓格子消失。
    /// </summary>
    private object HandleDigSpot(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var x = GetParamOr(p, "x", -1);
        var y = GetParamOr(p, "y", -1);
        if (x < 0 || y < 0)
            return new { ok = false, error = "need x,y" };

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var farmer = Game1.player;
                var loc = farmer.currentLocation;
                var tv = new Vector2(x, y);
                if (!loc.objects.TryGetValue(tv, out var obj) || obj.QualifiedItemId != "(O)590")
                {
                    tcs.SetResult(new { ok = false, error = "该格不是蚯蚓格子(Artifact Spot)", found = obj?.QualifiedItemId ?? obj?.Name ?? "空" });
                    return;
                }
                loc.digUpArtifactSpot(x, y, farmer);   // void：挖出的物品掉地上（有吸附，玩家在旁自动进包）
                tcs.SetResult(new { ok = true, location = loc.Name });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// GET /water?x=&y=&radius=
    /// 扫当前地点 (x,y) 半径内所有水瓦片（isWaterTile）——🦀 蟹笼部署找水/河流农场用（2026-08-16）。
    /// 默认以玩家位置为中心、radius=15。返回水瓦片坐标列表。
    /// </summary>
    private object HandleWater(HttpListenerContext ctx)
    {
        var qs = ctx.Request.QueryString;
        int x = int.TryParse(qs["x"], out var vx) ? vx : Game1.player.TilePoint.X;
        int y = int.TryParse(qs["y"], out var vy) ? vy : Game1.player.TilePoint.Y;
        int radius = int.TryParse(qs["radius"], out var vr) ? Math.Max(1, Math.Min(vr, 30)) : 15;

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var loc = Game1.player.currentLocation;
                var mapW = loc.Map.DisplayWidth / 64;
                var mapH = loc.Map.DisplayHeight / 64;
                var water = new List<object>();
                for (int dy = -radius; dy <= radius; dy++)
                {
                    for (int dx = -radius; dx <= radius; dx++)
                    {
                        int tx = x + dx, ty = y + dy;
                        if (tx < 0 || ty < 0 || tx >= mapW || ty >= mapH) continue;
                        if (loc.isWaterTile(tx, ty))
                            water.Add(new { x = tx, y = ty });
                    }
                }
                tcs.SetResult(new { ok = true, location = loc.Name, count = water.Count, water });
            }
            catch (Exception ex) { tcs.SetResult(new { ok = false, error = ex.Message }); }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    private object HandleCraft(HttpListenerContext ctx)
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var p = ReadJson(ctx);
        var name = GetParam<string>(p, "name");
        var count = GetParamOr(p, "count", 1);

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var farmer = Game1.player;
                var recipes = CraftingRecipe.craftingRecipes;
                if (!recipes.ContainsKey(name))
                {
                    var known = farmer.craftingRecipes.Keys.ToList();
                    tcs.SetResult(new { ok = false, error = $"Recipe '{name}' not found",
                        knownRecipes = known });
                    return;
                }

                if (!farmer.craftingRecipes.ContainsKey(name))
                {
                    tcs.SetResult(new { ok = false, error = $"Player hasn't learned recipe '{name}'" });
                    return;
                }

                var recipe = new CraftingRecipe(name, false);
                int crafted = 0;
                var missing = new Dictionary<string, int>();

                for (int i = 0; i < count; i++)
                {
                    if (!recipe.doesFarmerHaveIngredientsInInventory())
                    {
                        foreach (var kvp in recipe.recipeList)
                        {
                            var ingredientId = kvp.Key;
                            var needed = kvp.Value;
                            var have = 0;
                            foreach (var item in farmer.Items)
                            {
                                if (item != null && (item.ParentSheetIndex.ToString() == ingredientId
                                    || item.Category.ToString() == ingredientId))
                                    have += item.Stack;
                            }
                            if (have < needed)
                            {
                                var ingredientName = ingredientId;
                                try { ingredientName = new StardewValley.Object(ingredientId, 1).DisplayName; } catch { }
                                missing[ingredientName] = needed - have;
                            }
                        }
                        break;
                    }
                    recipe.consumeIngredients(null);
                    var product = recipe.createItem();
                    if (!farmer.addItemToInventoryBool(product))
                    {
                        Game1.createItemDebris(product, farmer.getStandingPosition(), farmer.FacingDirection);
                        tcs.SetResult(new { ok = true, crafted = crafted + 1,
                            warning = "Inventory full, item dropped" });
                        return;
                    }
                    crafted++;
                }

                if (crafted == 0)
                    tcs.SetResult(new { ok = false, error = "Missing materials", missing });
                else if (crafted < count)
                    tcs.SetResult(new { ok = true, crafted, requested = count,
                        warning = "Ran out of materials", missing });
                else
                    tcs.SetResult(new { ok = true, crafted });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// POST /cook  { "name": "Fried Egg", "count": 1 }
    /// Cook a recipe. Must be in the farmhouse with a kitchen upgrade (or use wizardry).
    /// Uses CraftingRecipe with isCooking=true.
    /// </summary>
    private object HandleCook(HttpListenerContext ctx)
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var p = ReadJson(ctx);
        var name = GetParam<string>(p, "name");
        var count = GetParamOr(p, "count", 1);

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var farmer = Game1.player;

                // Check cooking recipes dictionary
                if (!CraftingRecipe.cookingRecipes.ContainsKey(name))
                {
                    var known = farmer.cookingRecipes.Keys.ToList();
                    tcs.SetResult(new { ok = false, error = $"Cooking recipe '{name}' not found",
                        knownRecipes = known });
                    return;
                }

                if (!farmer.cookingRecipes.ContainsKey(name))
                {
                    tcs.SetResult(new { ok = false, error = $"Player hasn't learned recipe '{name}'" });
                    return;
                }

                var recipe = new CraftingRecipe(name, true);
                int crafted = 0;
                var missing = new Dictionary<string, int>();

                for (int i = 0; i < count; i++)
                {
                    if (!recipe.doesFarmerHaveIngredientsInInventory())
                    {
                        foreach (var kvp in recipe.recipeList)
                        {
                            var ingredientId = kvp.Key;
                            var needed = kvp.Value;
                            var have = 0;
                            foreach (var item in farmer.Items)
                            {
                                if (item != null && (item.QualifiedItemId == ingredientId
                                    || item.Category.ToString() == ingredientId
                                    || item.ParentSheetIndex.ToString() == ingredientId))
                                    have += item.Stack;
                            }
                            if (have < needed)
                            {
                                var ingredientName = ingredientId;
                                try { ingredientName = ItemRegistry.Create(ingredientId, 1).DisplayName; } catch { try { ingredientName = new StardewValley.Object(ingredientId, 1).DisplayName; } catch { } }
                                missing[ingredientName] = needed - have;
                            }
                        }
                        break;
                    }
                    recipe.consumeIngredients(null);
                    var product = recipe.createItem();
                    if (!farmer.addItemToInventoryBool(product))
                    {
                        Game1.createItemDebris(product, farmer.getStandingPosition(), farmer.FacingDirection);
                        tcs.SetResult(new { ok = true, crafted = crafted + 1,
                            warning = "Inventory full, item dropped" });
                        return;
                    }
                    crafted++;
                }

                if (crafted == 0)
                    tcs.SetResult(new { ok = false, error = "Missing materials", missing });
                else if (crafted < count)
                    tcs.SetResult(new { ok = true, crafted, requested = count,
                        warning = "Ran out of materials", missing });
                else
                    tcs.SetResult(new { ok = true, crafted });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// GET /recipes
    /// List all cooking recipes the player knows, with ingredient availability.
    /// </summary>
    private object HandleRecipes()
    {
        if (!Context.IsWorldReady)
            return new { ok = false, error = "World not ready" };

        try
        {
            var farmer = Game1.player;
            var recipesList = new List<object>();

            foreach (var kvp in CraftingRecipe.cookingRecipes)
            {
                var recipeName = kvp.Key;
                bool known = farmer.cookingRecipes.ContainsKey(recipeName);

                if (!known) continue;  // only show known recipes

                var recipe = new CraftingRecipe(recipeName, true);
                var ingredients = new List<object>();
                bool allHave = true;

                foreach (var ing in recipe.recipeList)
                {
                    var ingId = ing.Key;
                    var needed = ing.Value;
                    var have = 0;
                    foreach (var item in farmer.Items)
                    {
                        if (item != null && (item.QualifiedItemId == ingId
                            || item.Category.ToString() == ingId
                            || item.ParentSheetIndex.ToString() == ingId))
                            have += item.Stack;
                    }
                    string ingName = ingId;
                    try { ingName = ItemRegistry.Create(ingId, 1).DisplayName; } catch { try { ingName = new StardewValley.Object(ingId, 1).DisplayName; } catch { } }

                    ingredients.Add(new { name = ingName, id = ingId, needed, have });
                    if (have < needed) allHave = false;
                }

                string? productName = null;
                try { productName = recipe.createItem()?.DisplayName; } catch { }

                recipesList.Add(new
                {
                    name = recipeName,
                    product = productName ?? recipeName,
                    canMake = allHave,
                    ingredients
                });
            }

            return new { ok = true, count = recipesList.Count, recipes = recipesList };
        }
        catch (Exception ex)
        {
            return new { ok = false, error = ex.Message };
        }
    }

    /// <summary>
    /// GET /craft_recipes — 已学**合成**配方清单（技能等级规则：farmer.craftingRecipes 只含已解锁的）。
    /// 2026-08-13 加：MCP craft/list_craftables 用。跟 /recipes（烹饪）分开。
    /// </summary>
    private object HandleCraftRecipes()
    {
        if (!Context.IsWorldReady)
            return new { ok = false, error = "World not ready" };
        try
        {
            var farmer = Game1.player;
            var recipesList = new List<object>();

            foreach (var kvp in CraftingRecipe.craftingRecipes)
            {
                var recipeName = kvp.Key;
                // ⚠️ 技能等级规则：farmer.craftingRecipes 只含已学配方，没学的不报（也造不了）
                if (!farmer.craftingRecipes.ContainsKey(recipeName)) continue;

                var recipe = new CraftingRecipe(recipeName, false);
                var ingredients = new List<object>();
                bool allHave = true;
                foreach (var ing in recipe.recipeList)
                {
                    var ingId = ing.Key;
                    var needed = ing.Value;
                    var have = 0;
                    foreach (var item in farmer.Items)
                    {
                        if (item != null && (item.QualifiedItemId == ingId
                            || item.Category.ToString() == ingId
                            || item.ParentSheetIndex.ToString() == ingId))
                            have += item.Stack;
                    }
                    string ingName = ingId;
                    try { ingName = ItemRegistry.Create(ingId, 1).DisplayName; } catch { try { ingName = new StardewValley.Object(ingId, 1).DisplayName; } catch { } }
                    ingredients.Add(new { name = ingName, id = ingId, needed, have });
                    if (have < needed) allHave = false;
                }

                string? productName = null;
                try { productName = recipe.createItem()?.DisplayName; } catch { }

                recipesList.Add(new
                {
                    name = recipeName,
                    product = productName ?? recipeName,
                    canMake = allHave,
                    ingredients
                });
            }

            return new { ok = true, count = recipesList.Count, recipes = recipesList };
        }
        catch (Exception ex)
        {
            return new { ok = false, error = ex.Message };
        }
    }

    private object HandleProcessGeode(HttpListenerContext ctx)
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var p = ReadJson(ctx);
        var type = GetParamOr<string>(p, "type", "").Trim();   // 可选：指定砸哪种（名称/ID）

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var farmer = Game1.player;
                // ⚠️ 2026-08-16 恒：砸晶球列表加 金色椰子(791)/谜之盒(887)/金色谜之盒(891)
                int[] geodeIds = { 535, 536, 537, 749, 791, 887, 891 };

                for (int i = 0; i < farmer.Items.Count; i++)
                {
                    if (farmer.Items[i] is StardewValley.Object obj && geodeIds.Contains(obj.ParentSheetIndex))
                    {
                        // type 过滤：给了就只砸匹配的（名称/DisplayName/ID 任一匹配）
                        if (!string.IsNullOrEmpty(type))
                        {
                            var oid = obj.QualifiedItemId ?? obj.itemId?.Value ?? obj.ParentSheetIndex.ToString();
                            bool hit = obj.Name.Equals(type, StringComparison.OrdinalIgnoreCase)
                                || obj.DisplayName.Equals(type, StringComparison.OrdinalIgnoreCase)
                                || oid.Equals(type, StringComparison.OrdinalIgnoreCase)
                                || obj.ParentSheetIndex.ToString() == type;
                            if (!hit) continue;
                        }
                        int COST = 25;
                        if (farmer.Money < COST)
                        {
                            tcs.SetResult(new { ok = false, error = $"Need {COST}g, have {farmer.Money}g" });
                            return;
                        }
                        farmer.Money -= COST;

                        // 一次只砸一颗，让 Game1.random 自然推进
                        var result = StardewValley.Utility.getTreasureFromGeode(obj);
                        if (!farmer.addItemToInventoryBool(result))
                            Game1.createItemDebris(result, farmer.getStandingPosition(), farmer.FacingDirection);

                        obj.Stack--;
                        if (obj.Stack <= 0)
                            farmer.Items[i] = null;

                        string desc = "";
                        try { desc = result.getDescription(); } catch { }

                        tcs.SetResult(new { ok = true, geode = obj.Name, count = 1,
                            cost = COST, remainingGold = farmer.Money,
                            result = result.DisplayName,
                            description = desc,
                            remainingGeodes = obj.Stack > 0 ? obj.Stack : 0 });
                        return;
                    }
                }
                tcs.SetResult(new { ok = false, error = "No geode in inventory" });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    private object HandleProcessGeodeBatch(HttpListenerContext ctx)
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var qs = ctx.Request.QueryString;
        int wanted = int.TryParse(qs["count"], out var c) ? Math.Max(1, c) : 1;

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var farmer = Game1.player;
                // ⚠️ 2026-08-16 恒：砸晶球列表加 金色椰子(791)/谜之盒(887)/金色谜之盒(891)
                int[] geodeIds = { 535, 536, 537, 749, 791, 887, 891 };
                const int COST = 25;

                // 先统计可用晶球数量
                var geodeSlots = new List<(int idx, int geodeId, int stack)>();
                for (int i = 0; i < farmer.Items.Count; i++)
                {
                    if (farmer.Items[i] is StardewValley.Object obj && geodeIds.Contains(obj.ParentSheetIndex))
                        geodeSlots.Add((i, obj.ParentSheetIndex, obj.Stack));
                }

                int totalAvailable = geodeSlots.Sum(g => g.stack);
                int wantedCount = Math.Min(wanted, totalAvailable);

                if (wantedCount == 0)
                {
                    tcs.SetResult(new { ok = false, error = "No geodes in inventory" });
                    return;
                }
                if (farmer.Money < COST)
                {
                    tcs.SetResult(new { ok = false, error = $"Not enough gold. Need at least {COST}g, have {farmer.Money}g" });
                    return;
                }

                var allResults = new List<object>();
                int actualProcessed = 0;
                int remaining = wantedCount;

                foreach (var (slotIdx, geodeId, _) in geodeSlots)
                {
                    if (remaining <= 0) break;
                    var slotObj = farmer.Items[slotIdx] as StardewValley.Object;
                    if (slotObj == null || !geodeIds.Contains(slotObj.ParentSheetIndex)) continue;

                    int take = Math.Min(remaining, slotObj.Stack);
                    int crackedInSlot = 0;
                    for (int s = 0; s < take; s++)
                    {
                        // 每次砸晶球前推进 GeodesCracked 计数器
                        Game1.stats.GeodesCracked++;
                        var freshGeode = (StardewValley.Object)ItemRegistry.Create(geodeId.ToString());
                        var item = StardewValley.Utility.getTreasureFromGeode(freshGeode);
                        // 放入背包，放不下就停止（不浪费晶球）
                        var overflow = farmer.addItemToInventory(item);
                        if (overflow != null)
                        {
                            Game1.stats.GeodesCracked--;
                            break;
                        }
                        actualProcessed++;
                        allResults.Add(new { itemName = item.DisplayName, itemId = item.ItemId });
                        slotObj.Stack--;
                        crackedInSlot++;
                        if (slotObj.Stack <= 0)
                        {
                            farmer.Items[slotIdx] = null;
                            break;
                        }
                    }
                    remaining -= crackedInSlot;
                }

                int actualCost = COST * actualProcessed;
                farmer.Money -= actualCost;
                tcs.SetResult(new { ok = true, processed = actualProcessed, cost = actualCost,
                    remainingGold = farmer.Money, results = allResults,
                    remainingGeodes = totalAvailable - actualProcessed });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    private object HandleMuseumDebug()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        try
        {
            var farmer = Game1.player;
            var list = new List<Dictionary<string, object?>>();
            foreach (var i in farmer.Items)
            {
                if (i == null) continue;
                var info = new Dictionary<string, object?>
                {
                    ["name"] = i.Name,
                    ["itemId"] = i.ItemId,
                    ["stack"] = i.Stack,
                };
                if (i is StardewValley.Object obj)
                {
                    info["type"] = obj.Type ?? "";
                    info["cat"] = obj.Category;
                }
                list.Add(info);
            }

            var keys = new List<string>();
            foreach (var k in farmer.archaeologyFound.Keys)
                keys.Add(k);

            var pieces = new List<Dictionary<string, object>>();
            try {
                foreach (var kvp in Game1.netWorldState.Value.MuseumPieces.Pairs)
                    pieces.Add(new Dictionary<string, object> {
                        ["x"] = (int)kvp.Key.X, ["y"] = (int)kvp.Key.Y,
                        ["itemId"] = kvp.Value
                    });
            } catch { }

            var positions = new List<Dictionary<string, object>>();
            try {
                var dict = farmer.archaeologyFound;
                string[] fns = {"field", "_field", "dictionary", "_dictionary", "_fields", "pairs", "Pairs"};
                foreach (var fn in fns) {
                    var fi = dict.GetType().GetField(fn,
                        System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance);
                    var raw = fi?.GetValue(dict) as System.Collections.IDictionary;
                    if (raw != null && raw.Count > 0) {
                        foreach (System.Collections.DictionaryEntry e in raw)
                            if (e.Value is int[] arr)
                                positions.Add(new Dictionary<string, object> {
                                    ["id"] = e.Key?.ToString() ?? "?",
                                    ["tileX"] = arr[0], ["tileY"] = arr[1]
                                });
                        break;
                    }
                }
            } catch { }

            return new { ok = true, inventory = list,
                archaeologyCount = keys.Count, archaeologyKeys = keys,
                museumPieces = pieces, artifactPositions = positions };
        }
        catch (Exception ex)
        {
            return new { ok = false, error = ex.Message };
        }
    }

    private object HandleMuseumTiles()
    {
        if (!Context.IsWorldReady)
            return new { ok = false, error = "World not ready" };

        try
        {
            var museum = Game1.getLocationFromName("ArchaeologyHouse");
            if (museum == null)
                return new { ok = false, error = "ArchaeologyHouse not found" };

            var tiles = new List<Dictionary<string, object>>();
            var layers = new List<string>();
            string[] propNames = { "MuseumDisplay", "Museum", "Display", "museumDisplay", "Action", "Touch" };
            string[] layerNames = { "Buildings", "Back", "Front", "AlwaysFront" };

            foreach (var ln in layerNames)
            {
                var lyr = museum.Map.GetLayer(ln);
                if (lyr == null) continue;
                layers.Add(ln);
                for (int x = 0; x < lyr.LayerWidth; x++)
                {
                    for (int y = 0; y < lyr.LayerHeight; y++)
                    {
                        var tile = lyr.Tiles[x, y];
                        if (tile == null) continue;
                        foreach (var pn in propNames)
                        {
                            if (tile.Properties.ContainsKey(pn))
                            {
                                tiles.Add(new Dictionary<string, object> {
                                    ["x"] = x, ["y"] = y,
                                    ["layer"] = ln,
                                    ["property"] = pn,
                                    ["value"] = tile.Properties[pn].ToString()
                                });
                                break;
                            }
                        }
                    }
                }
            }

            // 如果没有 MuseumDisplay 属性，试试用 known vanilla positions
            if (tiles.Count == 0)
            {
                // SDV 1.6 ArchaeologyHouse 标准展位（来自地图数据）
                var knownSlots = new (int, int)[] {
                    (26,11),(27,11),(28,11),(29,11),(30,11),(31,11),(32,11),(33,11),(34,11),(35,11),
                    (27,8),(28,8),(29,8),(30,8),(31,8),(32,8),(33,8),(34,8),(35,8),
                    (27,6),(28,6),(29,6),(30,6),(31,6),(32,6),(33,6),(34,6),(35,6),
                    (27,5),(28,5),(29,5),(30,5),(31,5),(32,5),(33,5),(34,5),(35,5),
                    (27,4),(28,4),(29,4),(30,4),(31,4),(32,4),(33,4),(34,4),(35,4),
                };
                // 检查 Buildings 图层上的 tile index 173 (展示柜) 作为兜底
                var bLayer = museum.Map.GetLayer("Buildings");
                int bw = bLayer?.LayerWidth ?? 0;
                int bh = bLayer?.LayerHeight ?? 0;
                // 兜底策略：用已知坐标
                foreach (var (sx, sy) in knownSlots)
                    tiles.Add(new Dictionary<string, object> {
                        ["x"] = sx, ["y"] = sy,
                        ["layer"] = "known",
                        ["property"] = "MuseumDisplay",
                        ["value"] = "restored"
                    });
            }

            return new { ok = true, location = "ArchaeologyHouse",
                tileCount = tiles.Count, layers = layers, tiles };
        }
        catch (Exception ex)
        {
            return new { ok = false, error = ex.Message };
        }
    }

    private object HandleMuseumDiag()
    {
        if (!Context.IsWorldReady)
            return new { ok = false, error = "World not ready" };

        try
        {
            var museum = Game1.getLocationFromName("ArchaeologyHouse");
            if (museum == null)
                return new { ok = false, error = "ArchaeologyHouse not found" };

            var result = new Dictionary<string, object>();

            // 1. 反射扫方法名
            var methods = new List<string>();
            foreach (var m in museum.GetType().GetMethods(
                System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic
                | System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Static))
            {
                string n = m.Name.ToLower();
                if (n.Contains("donat") || n.Contains("spot") || n.Contains("piece")
                    || n.Contains("display") || n.Contains("shelf"))
                {
                    var ps = string.Join(", ", m.GetParameters().Select(p => $"{p.ParameterType.Name} {p.Name}"));
                    methods.Add($"{m.DeclaringType?.Name ?? "?"}.{m.Name}({ps})");
                }
            }
            result["methods"] = methods;

            // 2. 导出 Buildings 层所有 tile
            var tiles = new List<Dictionary<string, object>>();
            var layer = museum.Map.GetLayer("Buildings");
            if (layer != null)
            {
                for (int x = 0; x < layer.LayerWidth; x++)
                    for (int y = 0; y < layer.LayerHeight; y++)
                        if (layer.Tiles[x, y] != null)
                            tiles.Add(new Dictionary<string, object> {
                                ["x"] = x, ["y"] = y,
                                ["idx"] = layer.Tiles[x, y].TileIndex,
                                ["sheet"] = layer.Tiles[x, y].TileSheet?.Id ?? ""
                            });
            }
            result["buildingsLayerTiles"] = tiles;

            // 3. 导出现有 museumPieces
            var pieces = new List<Dictionary<string, object>>();
            try
            {
                var np = Game1.netWorldState.Value.MuseumPieces;
                if (np != null)
                    foreach (var kvp in np.Pairs)
                        pieces.Add(new Dictionary<string, object> {
                            ["x"] = (int)kvp.Key.X, ["y"] = (int)kvp.Key.Y,
                            ["itemId"] = kvp.Value?.ToString() ?? ""
                        });
            }
            catch { }
            result["existingPieces"] = pieces;

            // 4. 相关字段
            var fields = new List<string>();
            try
            {
                foreach (var f in museum.GetType().GetFields(
                    System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic
                    | System.Reflection.BindingFlags.Instance))
                {
                    if (f.Name.ToLower().Contains("piece") || f.Name.ToLower().Contains("donat")
                        || f.Name.ToLower().Contains("museum"))
                    {
                        var val = f.GetValue(museum);
                        fields.Add($"{f.Name}={val?.GetType().Name ?? "null"}");
                    }
                }
            }
            catch { }
            result["relatedFields"] = fields;

            return new { ok = true, locationType = museum.GetType().FullName, diagnosis = result };
        }
        catch (Exception ex)
        {
            return new { ok = false, error = ex.Message };
        }
    }

    private object HandleMuseumRemove(HttpListenerContext ctx)
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var qs = ctx.Request.QueryString;
        string id = qs["id"] ?? "";
        if (string.IsNullOrEmpty(id))
            return new { ok = false, error = "Missing ?id=" };

        var farmer = Game1.player;
        EnqueueMainThread(() =>
        {
            if (farmer.archaeologyFound.ContainsKey(id))
            {
                var pos = farmer.archaeologyFound[id];
                farmer.archaeologyFound.Remove(id);
                var vec = new Vector2(pos[0], pos[1]);
                var np = Game1.netWorldState.Value.MuseumPieces;
                if (np.ContainsKey(vec))
                    np.Remove(vec);
            }
        });
        return new { ok = true, removed = id };
    }

    // ── Quest System ──

    /// <summary>是否存储箱（宝箱/大箱子/石箱）。
    /// 只排除祝尼魔箱(JunimoChest)和迷你出货箱(MiniShippingBin)——出货箱会把物品当出货卖掉、祝尼魔箱内容互通。
    /// 大箱子=SpecialChestType.BigChest 是存储箱，必须保留（曾误杀：!=None 把大箱子全滤掉了）。
    /// 运行时它们都是 Chest 类型，靠 SpecialChestType / itemId 区分（130宝箱/232石箱/BigStoneChest大箱/248迷你出货箱）。</summary>
    private static bool IsStorageChest(Chest c)
    {
        // 语义过滤：SpecialChestType 属性（反射读，防编译依赖）
        try
        {
            var prop = typeof(Chest).GetProperty("SpecialChestType");
            if (prop != null)
            {
                var v = prop.GetValue(c)?.ToString() ?? "None";
                if (v == "JunimoChest" || v == "MiniShippingBin")
                    return false;
            }
        }
        catch { }
        // 兜底：迷你出货箱 itemId=248
        try
        {
            var id = c.itemId?.Value ?? "";
            if (id == "248") return false;
        }
        catch { }
        return true;
    }

    /// <summary>收集当前场景的存储箱（宝箱/大箱子/石箱 + FarmHouse/Cabin 内置冰箱）。
    /// 返回 (箱, 瓦片, 显示名)：显示名 label 给内置冰箱合成"内置冰箱"（不改存档，供名字匹配/报告）。
    /// 内置冰箱是 FarmHouse.fridge 字段（Cabin : FarmHouse），不在 loc.objects，单独补上。</summary>
    private static List<(Chest c, Vector2 tile, string label)> CollectStorageChests(GameLocation loc)
    {
        var list = new List<(Chest, Vector2, string)>();
        foreach (var kv in loc.objects.Pairs)
            if (kv.Value is Chest ch && IsStorageChest(ch))
                list.Add((ch, kv.Key, ""));
        try
        {
            if (loc is FarmHouse fh && fh.fridge?.Value is Chest fr && IsStorageChest(fr))
                list.Add((fr, fr.TileLocation, "内置冰箱"));
        }
        catch { }
        return list;
    }

    /// <summary>箱子染色 hex（#RRGGBB）。反射读 playerChoiceColor（NetColor），拿不到/透明给空串。</summary>
    private static string ChestColorHex(Chest c)
    {
        try
        {
            var f = typeof(Chest).GetField("playerChoiceColor", BindingFlags.Public | BindingFlags.Instance);
            if (f == null) return "";
            object? v = f.GetValue(c);
            if (v is Color direct)
            {
                if (direct.A == 0) return "";
                return $"#{direct.R:X2}{direct.G:X2}{direct.B:X2}";
            }
            if (v != null)
            {
                var vp = v.GetType().GetProperty("Value", BindingFlags.Public | BindingFlags.Instance);
                var col = vp?.GetValue(v);
                if (col is Color cr)
                {
                    if (cr.A == 0) return "";
                    return $"#{cr.R:X2}{cr.G:X2}{cr.B:X2}";
                }
            }
        }
        catch { }
        return "";
    }

    /// <summary>箱子自定义名（SDV 1.6 可改名），默认物品名当空串。</summary>
    private static string ChestName(Chest c)
    {
        try
        {
            var n = c.Name;
            if (string.IsNullOrWhiteSpace(n) || IsDefaultItemName(n)) return "";
            return n;
        }
        catch { return ""; }
    }

    /// <summary>箱子默认物品名（没被 AI/玩家自定义过）。标记名如 "宝箱(矿石)" 不算默认。</summary>
    private static readonly HashSet<string> _defaultChestNames = new(StringComparer.OrdinalIgnoreCase)
    {
        "Chest", "Big Chest", "BigChest", "Stone Chest", "Big Stone Chest", "Mini-Fridge", "Mini Fridge", "Fridge",
        "Mini-Shipping Bin", "Junimo Chest",
    };

    private static bool IsDefaultItemName(string? n)
    {
        if (string.IsNullOrEmpty(n)) return true;
        return _defaultChestNames.Contains(n);
    }

    /// <summary>箱子标记后的本名：去掉尾部 (xxx)。默认物品名 → 用本地化显示名（宝箱/迷你冰箱/石箱…）。</summary>
    private static string ChestBaseName(Chest c)
    {
        var cur = (c.Name ?? "").Trim();
        var baseName = cur;
        int paren = cur.LastIndexOf('(');
        if (paren > 0 && cur.EndsWith(")"))
            baseName = cur.Substring(0, paren).Trim();
        if (string.IsNullOrEmpty(baseName) || IsDefaultItemName(baseName))
            baseName = (c.DisplayName ?? "").Trim();
        if (string.IsNullOrEmpty(baseName)) baseName = "箱";
        return baseName;
    }

    /// <summary>显示名：有标记/自定义名 → 用 Name；否则内置冰箱用合成 label，其余用 ChestName。</summary>
    private static string DisplayChestName(Chest c, string label)
    {
        try
        {
            var n = c.Name ?? "";
            if (!string.IsNullOrEmpty(n) && !IsDefaultItemName(n))
                return n;   // 已标记/自定义
        }
        catch { }
        return label != "" ? label : ChestName(c);
    }

    private static (int, int, int) ParseHex(string hex)
    {
        hex = (hex ?? "").TrimStart('#');
        if (hex.Length < 6) return (0, 0, 0);
        try
        {
            return (Convert.ToInt32(hex.Substring(0, 2), 16),
                    Convert.ToInt32(hex.Substring(2, 2), 16),
                    Convert.ToInt32(hex.Substring(4, 2), 16));
        }
        catch { return (0, 0, 0); }
    }

    private static string ReflectField(object obj, string field, string def = "")
    {
        if (obj == null) return def;
        try
        {
            var f = obj.GetType().GetField(field, System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Instance);
            if (f == null) return def;
            var val = f.GetValue(obj);
            if (val == null) return def;
            // 处理 Netcode 类型
            var valType = val.GetType();
            var valProp = valType.GetProperty("Value", System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Instance);
            if (valProp != null)
                return valProp.GetValue(val)?.ToString() ?? def;
            return val.ToString() ?? def;
        }
        catch { return def; }
    }

    private static string DumpObj(object obj)
    {
        if (obj == null) return "";
        var parts = new List<string>();
        foreach (var f in obj.GetType().GetFields(System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Instance))
        {
            try
            {
                var val = f.GetValue(obj);
                string sv;
                if (val == null) sv = "null";
                else
                {
                    var valProp = val.GetType().GetProperty("Value", System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Instance);
                    sv = valProp != null ? (valProp.GetValue(val)?.ToString() ?? "null") : val.ToString() ?? "null";
                }
                if (sv.Length > 60) sv = sv.Substring(0, 60) + "...";
                parts.Add($"{f.Name}={sv}");
            }
            catch { parts.Add($"{f.Name}=<err>"); }
        }
        return string.Join(" | ", parts);
    }

    private object HandleQuestList()
    {
        if (!Context.IsWorldReady)
            return new { ok = false, error = "World not ready" };

        try
        {
            var farmer = Game1.player;
            var team = farmer.team;
            var quests = new List<object>();

            // 已接常规任务 — 用反射读所有字段
            foreach (var q in farmer.questLog)
            {
                if (q == null) continue;
                quests.Add(new Dictionary<string, object?> {
                    ["dump"] = DumpObj(q),
                    ["source"] = "questLog"
                });
            }

            // 已接特殊订单
            if (team?.specialOrders != null)
            {
                foreach (var so in team.specialOrders)
                {
                    if (so == null) continue;
                    quests.Add(new Dictionary<string, object?> {
                        ["dump"] = DumpObj(so),
                        ["source"] = "specialOrders",
                        ["accepted"] = true,
                    });
                }
            }

            // 可接特殊订单（布告板）
            if (team?.availableSpecialOrders != null)
            {
                foreach (var so in team.availableSpecialOrders)
                {
                    if (so == null) continue;
                    quests.Add(new Dictionary<string, object?> {
                        ["dump"] = DumpObj(so),
                        ["source"] = "availableSpecialOrders",
                        ["accepted"] = false,
                    });
                }
            }

            return new { ok = true, count = quests.Count, quests };
        }
        catch (Exception ex)
        {
            return new { ok = false, error = ex.Message };
        }
    }

    /// ⛔ 2026-08-22 已退役（路由已注释）：接单改走板上 menu click。下次重编 DLL 直接删此方法。
    private object HandleQuestAccept(HttpListenerContext ctx)
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var p = ReadJson(ctx);
        var questId = GetParam<string>(p, "id");

        if (string.IsNullOrEmpty(questId))
            return new { ok = false, error = "Missing quest id" };

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var farmer = Game1.player;
                var team = farmer.team;

                // 查特殊订单
                if (team?.availableSpecialOrders != null)
                {
                    foreach (var so in team.availableSpecialOrders)
                    {
                        if (so == null) continue;
                        string soId = ReflectField(so, "orderId");
                        // 🆕 2026-08-18 orderId 可能非 public 反射不到 → questKey 兜底（SpecialOrder 的 data key，可靠）
                        if (string.IsNullOrEmpty(soId)) { try { soId = so.questKey?.Value ?? ""; } catch { } }
                        if (soId == questId)
                        {
                            team.availableSpecialOrders.Remove(so);
                            team.specialOrders.Add(so);
                            tcs.SetResult(new { ok = true, accepted = "SpecialOrder",
                                id = questId, title = ReflectField(so, "requester", "?") + " 订单" });
                            return;
                        }
                    }
                }

                tcs.SetResult(new { ok = false, error = $"Quest '{questId}' not found or not available" });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// GET /quest_progress
    /// Returns detailed quest progress with proper sub-class dispatching.
    /// Covers: questLog (all types), specialOrders (active + available), questOfTheDay.
    /// </summary>
    private object HandleQuestProgress()
    {
        if (!Context.IsWorldReady)
            return new { ok = false, error = "World not ready" };

        try
        {
            var farmer = Game1.player;
            var team = farmer.team;
            var quests = new List<object>();

            // ── 1. 今日求助栏任务 (Help Wanted / questOfTheDay) ──
            var dailyQuest = Game1.questOfTheDay;
            if (dailyQuest != null)
            {
                quests.Add(new Dictionary<string, object?>
                {
                    ["source"] = "questOfTheDay",
                    ["id"] = dailyQuest.id.Value,
                    ["title"] = dailyQuest.questTitle,
                    ["description"] = dailyQuest.questDescription,
                    ["daysLeft"] = dailyQuest.daysLeft.Value,
                    ["moneyReward"] = dailyQuest.moneyReward.Value,
                    ["completed"] = dailyQuest.completed.Value,
                    ["type"] = dailyQuest.GetType().Name
                });
            }

            // ── 2. 已接常规任务 — 按子类分发读进度 ──
            foreach (var quest in farmer.questLog)
            {
                if (quest == null) continue;

                var info = new Dictionary<string, object?>
                {
                    ["source"] = "questLog",
                    ["id"] = quest.id.Value,
                    ["title"] = quest.questTitle,
                    ["description"] = quest.questDescription,
                    ["completed"] = quest.completed.Value,
                    ["daysLeft"] = quest.daysLeft.Value,
                    ["moneyReward"] = quest.moneyReward.Value,
                };

                // 按子类分发（反射读取，兼容SDV 1.6 API变动）
                var typeName = quest.GetType().Name;
                info["type"] = typeName;
                var qobj = (object)quest;

                info["target"] = ReflectField(qobj, "monsterName", "")
                               + ReflectField(qobj, "target", "");
                info["killed"] = ReflectField(qobj, "numberKilled", "0");
                info["caught"] = ReflectField(qobj, "numberFished", "0");
                info["collected"] = ReflectField(qobj, "numberCollected", "0");
                info["required"] = ReflectField(qobj, "numberToKill", "0")
                                 + ReflectField(qobj, "numberToFish", "0")
                                 + ReflectField(qobj, "number", "0");
                info["itemId"] = ReflectField(qobj, "itemId", "")
                               + ReflectField(qobj, "resource", "")
                               + ReflectField(qobj, "item", "");
                info["targetNPC"] = ReflectField(qobj, "target", "");

                quests.Add(info);
            }

            // ── 3. 已接特殊订单 ──
            if (team?.specialOrders != null)
            {
                foreach (var so in team.specialOrders)
                {
                    if (so == null) continue;
                    var info = new Dictionary<string, object?>
                    {
                        ["source"] = "specialOrders",
                        ["requester"] = so.requester.Value,
                        ["dueDate"] = so.dueDate.Value,
                        ["state"] = so.questState.Value.ToString(),
                        ["description"] = so.GetDescription(),
                    };
                    // duration (SDV 1.6: may be durationValue or duration)
                    var dur = ReflectField(so, "durationValue", "");
                    if (string.IsNullOrEmpty(dur)) dur = ReflectField(so, "duration", "");
                    info["duration"] = dur;
                    info["days_left"] = so.GetDaysLeft(); // 用SDV内置算法算剩余天数

                    // 读 objectives
                    var objList = new List<object>();
                    if (so.objectives != null)
                    {
                        foreach (var obj in so.objectives)
                        {
                            if (obj == null) continue;
                            var desc = obj.GetDescription();
                            // 如果返回的是 localization key 格式（含 [ 或 Objective 关键词），尝试手动解析
                            if (desc.StartsWith("[") || desc.Contains("Objective"))
                            {
                                try
                                {
                                    // 尝试从游戏数据直接读 SpecialOrderStrings 表
                                    var stringData = Game1.content.Load<Dictionary<string, string>>("Strings\\SpecialOrderStrings");
                                    var rawKey = desc.Trim('[', ']');
                                    if (stringData.TryGetValue(rawKey, out var resolvedText))
                                    {
                                        if (!string.IsNullOrEmpty(resolvedText))
                                            desc = resolvedText;
                                    }
                                }
                                catch { }
                            }
                            objList.Add(new
                            {
                                description = desc,
                                currentCount = obj.currentCount.Value,
                                maxCount = obj.maxCount.Value,
                                complete = obj.IsComplete()
                            });
                        }
                    }
                    info["objectives"] = objList;
                    quests.Add(info);
                }
            }

            // ── 4. 可接特殊订单 ──
            if (team?.availableSpecialOrders != null)
            {
                foreach (var so in team.availableSpecialOrders)
                {
                    if (so == null) continue;
                    var info = new Dictionary<string, object?>
                    {
                        ["source"] = "availableSpecialOrders",
                        ["requester"] = so.requester.Value,
                        ["description"] = so.GetDescription(),
                    };
                    // 可用订单的时长在 questDuration 字段（如 "Month", "TwoWeeks"）
                    var durStr = ReflectField(so, "questDuration", "?");
                    var durDays = durStr switch
                    {
                        "OneDay" => 1, "ThreeDays" => 3,
                        "OneWeek" or "Week" => 7,
                        "TwoWeeks" => 14, "Month" => 28, "TwoMonths" => 56,
                        _ => 0
                    };
                    info["duration"] = durStr;
                    info["duration_days"] = durDays > 0 ? durDays.ToString() : durStr;
                    info["days_left"] = null;
                    quests.Add(info);
                }
            }

            // ── 5. 将 days_left 也写入已接特殊订单 ──
            // （上面已通过 info["days_left"] = so.GetDaysLeft() 写入）

            return new { ok = true, count = quests.Count, quests };
        }
        catch (Exception ex)
        {
            return new { ok = false, error = ex.Message };
        }
    }

    // ── Qi Gem Shop ──

    private object HandleQiShop()
    {
        if (!Context.IsWorldReady)
            return new { ok = false, error = "World not ready" };

        try
        {
            var farmer = Game1.player;
            int qiGems = 0;
            try { qiGems = farmer.QiGems; } catch { }

            // 尝试从 Data/Shops 读取齐钻商店数据
            var items = new List<object>();
            try
            {
                var shopData = Game1.content.Load<Dictionary<string, string>>("Data/Shops");
                if (shopData.TryGetValue("QiGemShop", out var raw))
                {
                    // Data/Shops 格式: owner/items...
                    var parts = raw.Split('/');
                    foreach (var part in parts)
                    {
                        if (part.Contains(' '))
                        {
                            var fields = part.Split(' ');
                            if (fields.Length >= 2 && int.TryParse(fields[0], out _))
                            {
                                string itemId = fields[0];
                                int price = int.TryParse(fields[1], out var p) ? p : 0;
                                string itemName = "?";
                                try
                                {
                                    var obj = ItemRegistry.Create(itemId);
                                    itemName = obj.DisplayName;
                                }
                                catch { }
                                items.Add(new Dictionary<string, object?> {
                                    ["id"] = itemId,
                                    ["name"] = itemName,
                                    ["price"] = price,
                                    ["currency"] = "QiGem"
                                });
                            }
                        }
                    }
                }
            }
            catch { }

            // 兜底：已知齐钻商店商品列表
            if (items.Count == 0)
            {
                var fallback = new (string id, string name, int price)[]
                {
                    ("(O)69", "枫糖浆 x1", 5),
                    ("(O)70", "橡树树脂 x1", 5),
                    ("(O)71", "松焦油 x1", 5),
                    ("(O)350", "万象晶球 x1", 5),
                    ("(O)645", "铱锭 x1", 10),
                    ("(O)337", "铱矿石 x1", 5),
                    ("(O)279", "芒果 x1", 20),
                    ("(O)835", "香蕉 x1", 20),
                    ("(O)446", "椰林飘香 x1", 20),
                    ("(O)237", "奶酪 x1 (铱星)", 10),
                    ("(O)346", "啤酒 x1", 50),
                    ("(O)303", "古物宝藏 x1", 25),
                    ("(O)73", "熔岩武士刀 x1", 50),
                    ("(O)60", "翡翠 x1", 40),
                    ("(O)62", "红宝石 x1", 40),
                    ("(O)64", "紫水晶 x1", 20),
                    ("(O)66", "海蓝宝石 x1", 20),
                    ("(O)68", "黄水晶 x1", 20),
                    ("(O)74", "棱晶碎片 x1", 80),
                    ("(O)110", "银河之剑 x1", 100),
                    ("(O)803", "银河匕首 x1", 100),
                    ("(O)804", "银河长戟 x1", 100),
                    ("(O)805", "银河铁锤 x1", 100),
                    ("(O)168", "银河魂石 x1", 50),
                    ("(O)688", "火药 x1", 10),
                    ("(O)128", "水晶果 x1", 10),
                    ("(O)44", "草莓种子 x1", 5),
                    ("(O)635", "上古种子 x1", 30),
                    ("(O)610", "齐钻 x1", 1),
                    ("(O)458", "怪物香水 x1", 30),
                    ("(O)459", "万能鱼饵 x1", 5),
                    ("(O)898", "加压喷头 x1", 20),
                    ("(O)899", "施肥喷头 x1", 20),
                    ("(O)900", "收获喷头 x1", 20),
                    ("(O)901", "取液喷头 x1", 20),
                    ("(O)872", "加料器 x1", 20),
                };
                foreach (var f in fallback)
                    items.Add(new Dictionary<string, object?> {
                        ["id"] = f.id, ["name"] = f.name,
                        ["price"] = f.price, ["currency"] = "QiGem"
                    });
            }

            return new { ok = true, qiGems, itemCount = items.Count, items };
        }
        catch (Exception ex)
        {
            return new { ok = false, error = ex.Message };
        }
    }

    private object HandleQiBuy(HttpListenerContext ctx)
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var p = ReadJson(ctx);
        var itemId = GetParam<string>(p, "id");
        var count = GetParamOr(p, "count", 1);

        if (string.IsNullOrEmpty(itemId))
            return new { ok = false, error = "Missing item id" };

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var farmer = Game1.player;

                // 查价格
                int unitPrice = 0;
                try
                {
                    var shopData = Game1.content.Load<Dictionary<string, string>>("Data/Shops");
                    if (shopData.TryGetValue("QiGemShop", out var raw))
                    {
                        foreach (var part in raw.Split('/'))
                        {
                            var fields = part.Split(' ');
                            if (fields.Length >= 2 && fields[0] == itemId)
                                unitPrice = int.TryParse(fields[1], out var pp) ? pp : 0;
                        }
                    }
                }
                catch { }

                // 如果从数据没读到，用兜底价格
                if (unitPrice == 0)
                {
                    var fallbackPrices = new Dictionary<string, int>
                    {
                        ["(O)69"] = 5, ["(O)70"] = 5, ["(O)71"] = 5, ["(O)350"] = 5,
                        ["(O)645"] = 10, ["(O)337"] = 5, ["(O)279"] = 20, ["(O)835"] = 20,
                        ["(O)446"] = 20, ["(O)237"] = 10, ["(O)346"] = 50, ["(O)303"] = 25,
                        ["(O)73"] = 50, ["(O)60"] = 40, ["(O)62"] = 40, ["(O)64"] = 20,
                        ["(O)66"] = 20, ["(O)68"] = 20, ["(O)74"] = 80, ["(O)110"] = 100,
                        ["(O)803"] = 100, ["(O)804"] = 100, ["(O)805"] = 100, ["(O)168"] = 50,
                        ["(O)688"] = 10, ["(O)128"] = 10, ["(O)44"] = 5, ["(O)635"] = 30,
                        ["(O)610"] = 1, ["(O)458"] = 30, ["(O)459"] = 5,
                        ["(O)898"] = 20, ["(O)899"] = 20, ["(O)900"] = 20, ["(O)901"] = 20,
                        ["(O)872"] = 20,
                    };
                    fallbackPrices.TryGetValue(itemId, out unitPrice);
                }
                if (unitPrice == 0)
                    unitPrice = 10; // 兜底

                int totalCost = unitPrice * count;
                if (farmer.QiGems < totalCost)
                {
                    tcs.SetResult(new { ok = false, error = $"Need {totalCost} Qi Gems, have {farmer.QiGems}" });
                    return;
                }

                farmer.QiGems -= totalCost;
                var item = ItemRegistry.Create(itemId, count);
                farmer.addItemByMenuIfNecessary(item);

                tcs.SetResult(new { ok = true, given = item.DisplayName, count,
                    cost = totalCost, currency = "QiGem",
                    remainingQiGems = farmer.QiGems });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    private object HandleClearGround(HttpListenerContext ctx)
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var qs = ctx.Request.QueryString;
        int tx = int.Parse(qs["x"] ?? "0");
        int ty = int.Parse(qs["y"] ?? "0");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var loc = Game1.currentLocation;
                var toRemove = new List<Debris>();
                foreach (var d in loc.debris)
                    if (d != null && d.Chunks.Count > 0
                        && (int)(d.Chunks[0].position.X / 64f) == tx
                        && (int)(d.Chunks[0].position.Y / 64f) == ty)
                        toRemove.Add(d);
                foreach (var d in toRemove)
                    loc.debris.Remove(d);
                var tileVec = new Vector2(tx, ty);
                if (loc.objects.ContainsKey(tileVec))
                    loc.objects.Remove(tileVec);
                // Also remove terrain features (trees, weeds, stones, etc.)
                if (loc.terrainFeatures.ContainsKey(tileVec))
                    loc.terrainFeatures.Remove(tileVec);
                // Remove large terrain features (like giant stumps) from resource clusters
                if (loc.largeTerrainFeatures?.Count > 0)
                {
                    var toRemoveLarge = loc.largeTerrainFeatures
                        .Where(ltf => ltf != null && (int)ltf.Tile.X == tx && (int)ltf.Tile.Y == ty).ToList();
                    foreach (var ltf in toRemoveLarge)
                        loc.largeTerrainFeatures.Remove(ltf);
                }
                tcs.SetResult(new { ok = true, cleared = new { x = tx, y = ty } });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    private object HandleDebris()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var loc = Game1.currentLocation;
                var debrisList = new List<object>();
                foreach (var d in loc.debris)
                {
                    if (d != null && d.Chunks.Count > 0)
                    {
                        string name = "?";
                        try
                        {
                            if (d.item != null)
                                name = d.item.Name ?? "";
                            else if (!string.IsNullOrEmpty(d.itemId?.Value))
                            {
                                // SDV 1.6: d.item 常为 null（炸矿/敲碎的 debris），用 itemId 兜底取名字
                                var dropItem = ItemRegistry.Create(d.itemId.Value, 1);
                                name = dropItem?.DisplayName ?? dropItem?.Name ?? d.itemId.Value;
                            }
                        }
                        catch { }
                        debrisList.Add(new
                        {
                            x = (int)(d.Chunks[0].position.X / 64f),
                            y = (int)(d.Chunks[0].position.Y / 64f),
                            itemName = name
                        });
                    }
                }
                tcs.SetResult(new { ok = true, location = loc.Name, debris = debrisList });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// POST /toggle_doors  { action: "close" }
    /// Toggle all animal building doors (open or close).
    /// </summary>
    private object HandleToggleDoors()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var farm = Game1.getFarm();
                int toggled = 0;
                var details = new List<object>();

                foreach (var building in farm.buildings)
                {
                    if (building == null) continue;
                    var bType = building.buildingType?.Value ?? "";
                    bool isAnimalBuilding = bType.Contains("Coop") || bType.Contains("Barn");
                    if (!isAnimalBuilding) continue;

                    try
                    {
                        // Try to toggle animal door via reflection on actual runtime type
                        var bType_runtime = building.GetType();
                        var doorField = bType_runtime.GetField("animalDoorOpen",
                            BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                        if (doorField != null && doorField.GetValue(building) is Netcode.NetBool netBool)
                        {
                            netBool.Value = !netBool.Value;
                            toggled++;
                            details.Add(new { building = bType, door_open = netBool.Value });
                        }
                        else
                        {
                            // Try method instead
                            var method = bType_runtime.GetMethod("ToggleAnimalDoor",
                                BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                            if (method != null)
                            {
                                method.Invoke(building, null);
                                toggled++;
                                details.Add(new { building = bType, toggled_via = "ToggleAnimalDoor" });
                            }
                            else
                            {
                                details.Add(new { building = bType, error = "no door field or method found",
                                    runtime_type = bType_runtime.Name });
                            }
                        }
                    }
                    catch (Exception ex)
                    {
                        details.Add(new { building = bType, error = ex.Message });
                    }
                }

                tcs.SetResult(new
                {
                    ok = true,
                    toggled,
                    details
                });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// GET /sprinklers
    /// Scans current location (or farm) for sprinklers and their coverage area.
    /// Returns sprinkler positions and the tiles they cover.
    /// </summary>
    private object HandleSprinklers()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var loc = Game1.currentLocation;
                var sprinklers = new List<object>();

                foreach (var obj in loc.Objects.Values)
                {
                    if (obj == null) continue;
                    var name = obj.Name;
                    int range = 0;
                    string type = "none";

                    if (name.Contains("Sprinkler"))
                    {
                        if (name.Contains("Iridium"))
                        { range = 2; type = "iridium"; }      // 5x5
                        else if (name.Contains("Quality"))
                        { range = 1; type = "quality"; }       // 3x3
                        else
                        { range = 1; type = "basic"; }         // 4 adjacent

                        var tiles = new List<Dictionary<string, int>>();
                        int ox = (int)obj.TileLocation.X;
                        int oy = (int)obj.TileLocation.Y;

                        if (type == "basic")
                        {
                            tiles.Add(new() { ["x"] = ox, ["y"] = oy - 1 });
                            tiles.Add(new() { ["x"] = ox, ["y"] = oy + 1 });
                            tiles.Add(new() { ["x"] = ox - 1, ["y"] = oy });
                            tiles.Add(new() { ["x"] = ox + 1, ["y"] = oy });
                        }
                        else
                        {
                            for (int dx = -range; dx <= range; dx++)
                                for (int dy = -range; dy <= range; dy++)
                                    if (dx != 0 || dy != 0)
                                        tiles.Add(new() { ["x"] = ox + dx, ["y"] = oy + dy });
                        }

                        sprinklers.Add(new
                        {
                            name,
                            type,
                            x = ox,
                            y = oy,
                            tiles
                        });
                    }
                }

                tcs.SetResult(new
                {
                    ok = true,
                    location = loc.Name,
                    count = sprinklers.Count,
                    sprinklers
                });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// POST /till_area
    /// Directly creates HoeDirt terrain features on specified tiles (bypasses tool animation).
    /// Body: { tiles: [{x,y}, ...], power?: 0-4 }
    ///       OR { x, y, length, direction, power? }
    /// </summary>
    private object HandleTillArea(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var loc = Game1.currentLocation;
                var farmer = Game1.player;
                var tiles = CalculateAreaTiles(p, farmer);
                var tilled = new List<Dictionary<string, int>>();
                var skipped = new List<Dictionary<string, object?>>();

                foreach (var (tx, ty) in tiles)
                {
                    var vec = new Vector2(tx, ty);
                    // Skip if already has HoeDirt or occupied
                    if (loc.terrainFeatures.ContainsKey(vec))
                    {
                        if (loc.terrainFeatures[vec] is HoeDirt)
                            skipped.Add(new() { ["x"] = tx, ["y"] = ty, ["reason"] = "already_tilled" });
                        else
                            skipped.Add(new() { ["x"] = tx, ["y"] = ty, ["reason"] = "occupied" });
                        continue;
                    }
                    if (loc.objects.ContainsKey(vec))
                    {
                        skipped.Add(new() { ["x"] = tx, ["y"] = ty, ["reason"] = "object" });
                        continue;
                    }
                    // ⚠️ 2026-08-13：不可耕地块不强行锄（恒：补漏把非耕地也改了=bug）
                    if (loc.doesTileHaveProperty(tx, ty, "Diggable", "Back") == null)
                    {
                        skipped.Add(new() { ["x"] = tx, ["y"] = ty, ["reason"] = "not_diggable" });
                        continue;
                    }

                    loc.terrainFeatures[vec] = new HoeDirt();
                    tilled.Add(new() { ["x"] = tx, ["y"] = ty });
                }

                tcs.SetResult(new
                {
                    ok = true,
                    location = loc.Name,
                    tilled_count = tilled.Count,
                    skipped_count = skipped.Count,
                    tilled,
                    skipped
                });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// ⚠️ /water_area 已删除（2026-08-15 恒：直接改 dirt.state 是作弊，会误导 AI）。
    /// 浇水一律 tool_area 蓄力 + 逐锚点/余数兜底（position+DoFunction 真浇），无直接改地块路径。
    /// </summary>

    /// <summary>
    /// Helper: parse tile list from request body.
    /// Accepts explicit {tiles:[{x,y}]} OR {x, y, length, direction, power}
    /// </summary>
    private List<(int x, int y)> CalculateAreaTiles(Dictionary<string, object?> p, Farmer farmer)
    {
        // Explicit tile list takes priority
        if (p.TryGetValue("tiles", out var tilesObj) && tilesObj != null)
        {
            var list = new List<(int x, int y)>();
            if (tilesObj is JsonElement je && je.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in je.EnumerateArray())
                {
                    int tx = item.GetProperty("x").GetInt32();
                    int ty = item.GetProperty("y").GetInt32();
                    list.Add((tx, ty));
                }
            }
            if (list.Count > 0) return list;
        }

        // Power-based area calculation
        int x = GetParamOr(p, "x", -1);
        int y = GetParamOr(p, "y", -1);
        int power = GetParamOr(p, "power", 0);
        int length = GetParamOr(p, "length", -1);
        string direction = GetParamOr(p, "direction", "");

        // Default: use player's facing tile if no coordinates given
        if (x < 0 || y < 0)
        {
            var facingTile = GetFacingTile(farmer);
            x = (int)facingTile.X;
            y = (int)facingTile.Y;
        }

        var result = new List<(int x, int y)>();

        if (length > 0 && !string.IsNullOrEmpty(direction))
        {
            // Line mode
            int dx = direction == "horizontal" ? 1 : 0;
            int dy = direction == "vertical" ? 1 : 0;
            for (int i = 0; i < length; i++)
                result.Add((x + dx * i, y + dy * i));
        }
        else
        {
            // Power-based area (matches tool upgrade levels)
            int dx = farmer.FacingDirection switch { 1 => 1, 3 => -1, _ => 0 };
            int dy = farmer.FacingDirection switch { 0 => -1, 2 => 1, _ => 0 };
            // Line perpendicular to facing direction
            int px = dy, py = dx; // perpendicular

            switch (power)
            {
                case 0: // 1 tile
                    result.Add((x, y));
                    break;
                case 1: // 3 tiles in a line (copper)
                    for (int i = -1; i <= 1; i++)
                        result.Add((x + px * i, y + py * i));
                    break;
                case 2: // 5 tiles in a line (steel)
                    for (int i = -2; i <= 2; i++)
                        result.Add((x + px * i, y + py * i));
                    break;
                case 3: // 3x3 square (gold)
                    for (int dx2 = -1; dx2 <= 1; dx2++)
                        for (int dy2 = -1; dy2 <= 1; dy2++)
                            result.Add((x + dx2, y + dy2));
                    break;
                case 4: // 3x6/6x3 rectangle (iridium)
                    for (int dx2 = 0; dx2 <= 2; dx2++)
                        for (int dy2 = -2; dy2 <= 2; dy2++)
                            result.Add((x + dx2, y + dy2));
                    break;
                default:
                    result.Add((x, y));
                    break;
            }
        }

        return result;
    }

    /// <summary>
    /// POST /tool_area
    /// Tool area operation with proper charge-release animation.
    /// Auto-detects target area if not specified.
    /// Body: { operation: "water"|"till", x1?, y1?, x2?, y2? }
    /// </summary>
    private object HandleToolArea(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var operation = GetParam<string>(p, "operation");

        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");
        if (operation != "water" && operation != "till")
            return new { ok = false, error = "operation must be 'water' or 'till'" };

        // 1. CALCULATION — runs on main thread (needs game state)
        var calcTcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var farmer = Game1.player;
                var loc = farmer.currentLocation;

                // Validate tool
                Tool? tool = farmer.CurrentTool;
                string requiredTool = operation == "water" ? "Watering Can" : "Hoe";
                if (tool == null || !tool.Name.Contains(requiredTool))
                {
                    var found = farmer.Items.OfType<Tool>()
                        .FirstOrDefault(t => t.Name.Contains(requiredTool));
                    if (found == null)
                    { calcTcs.SetResult(new { ok = false, error = $"No {requiredTool} in inventory" }); return; }
                    farmer.CurrentToolIndex = farmer.Items.IndexOf(found);
                    tool = found;
                }

                int upgradeLevel = 0;
                try { upgradeLevel = tool.UpgradeLevel; } catch { }

                var (toolW, toolH, chargeFrames) = upgradeLevel switch
                {
                    // toolW=垂直宽, toolH=面向距离（实测2026-08-02：宽3距离6，非6宽3距离）
                    0 => (1, 1, 0), 1 => (1, 3, 20), 2 => (1, 5, 40),
                    3 => (3, 3, 60), 4 => (3, 6, 80), _ => (1, 1, 0)
                };

                // Determine target tiles
                int x1 = GetParamOr(p, "x1", -1), y1 = GetParamOr(p, "y1", -1);
                int x2 = GetParamOr(p, "x2", -1), y2 = GetParamOr(p, "y2", -1);
                List<(int tx, int ty)> targetTiles = new();

                if (x1 >= 0 && y1 >= 0 && x2 >= 0 && y2 >= 0)
                {
                    // ⚠️ 2026-08-13：矩形路径加 Diggable 校验——只锄可耕地，路径/建筑/水不碰（恒：栅栏外凭空造土块=bug）
                    for (int x = Math.Min(x1, x2); x <= Math.Max(x1, x2); x++)
                        for (int y = Math.Min(y1, y2); y <= Math.Max(y1, y2); y++)
                            if (loc.doesTileHaveProperty(x, y, "Diggable", "Back") != null)
                                targetTiles.Add((x, y));
                }
                else
                {
                    for (int dx = -25; dx <= 25; dx++)
                        for (int dy = -25; dy <= 25; dy++)
                        {
                            int cx = farmer.TilePoint.X + dx, cy = farmer.TilePoint.Y + dy;
                            var vec = new Vector2(cx, cy);
                            if (operation == "water")
                            {
                                if (loc.terrainFeatures.TryGetValue(vec, out var tf) && tf is HoeDirt dirt
                                    && dirt.crop != null && dirt.state.Value == 0)
                                    targetTiles.Add((cx, cy));
                            }
                            else
                            {
                                if (!loc.terrainFeatures.ContainsKey(vec) && !loc.objects.ContainsKey(vec)
                                    && loc.doesTileHaveProperty(cx, cy, "Diggable", "Back") != null)
                                    targetTiles.Add((cx, cy));
                            }
                        }
                }

                if (targetTiles.Count == 0)
                { calcTcs.SetResult(new { ok = false, error = $"No {(operation == "water" ? "unwatered crops" : "diggable tiles")} nearby" }); return; }

                // Calculate anchors (蓄力锚点生成，till/water 共用；补漏轮次也用它)
                var commands = BuildToolAreaCommands(targetTiles, toolW, toolH, chargeFrames, upgradeLevel);
                int minX = targetTiles.Min(t => t.tx), maxX = targetTiles.Max(t => t.tx);
                int minY = targetTiles.Min(t => t.ty), maxY = targetTiles.Max(t => t.ty);

                // Set up queue (main thread will process it tick by tick)
                _commandQueueTcs = new TaskCompletionSource<object>();
                _commandResults.Clear();
                _commandQueue = new Queue<Dictionary<string, object?>>(commands);
                _commandDelay = 0;
                _waitingForMove = false;
                // 存补漏参数（2026-08-15 恒：蓄力补漏用，不直接改地块）
                _toolAreaTargets = targetTiles;
                _toolAreaOperation = operation;
                _toolAreaToolW = toolW; _toolAreaToolH = toolH;
                _toolAreaChargeFrames = chargeFrames; _toolAreaUpgradeLevel = upgradeLevel;
                _toolAreaTotalSwings = commands.Count / 3;

                calcTcs.SetResult(new
                {
                    ok = true, operation, tool = tool.Name, upgrade_level = upgradeLevel,
                    target_area = new { x1 = minX, y1 = minY, x2 = maxX, y2 = maxY },
                    sw_width = toolW, sw_height = toolH, charge_frames = chargeFrames,
                    swings = commands.Count / 3  // move + face + charge per swing
                });
            }
            catch (Exception ex) { calcTcs.SetResult(new { ok = false, error = ex.Message }); }
        });

        // Wait for calc to finish (HTTP thread — fine)
        var calcResult = calcTcs.Task.GetAwaiter().GetResult();

        // 2. EXECUTION — wait on HTTP thread, NOT in EnqueueMainThread
        if (_commandQueueTcs != null)
        {
            bool completed = _commandQueueTcs.Task.Wait(TimeSpan.FromMinutes(10));
            var result = completed ? _commandQueueTcs.Task.Result
                : new { ok = false, error = "Tool area operation timed out" };

            // 3. 补漏（取余补站位逐格，2026-08-15 恒：main thread 直接 DoFunction，不走 queue/charge——
            //    实测 charge 蓄力等待期间位置可能漂移、fallback 从错位算导致浇不上（73,20-22 列案例）；
            //    直接 DoFunction（等效 /tool）position 站位可靠。仍漏的报 still_missing 原因）
            int patches = 0;
            if (completed && (operation == "till" || operation == "water"))
            {
                for (int round = 0; round < 4; round++)
                {
                    var missing = FindMissingToolAreaTiles(operation);
                    if (missing.Count == 0) break;
                    int fixedNow = PatchMissingOnMain(operation, missing.Select(m => (m.tx, m.ty)).ToList());
                    patches += fixedNow;
                    if (fixedNow == 0) break;   // 一轮都补不上（被包围/水挡）→ 报告原因
                }
            }

            // 4. 最终仍漏（报告给 Python，AI 决定下一步；附原因——2026-08-15 恒：加报错原因）
            var still = completed ? FindMissingToolAreaTiles(operation) : new List<(int tx, int ty, string reason)>();
            var stillList = still.Select(t => new { x = t.tx, y = t.ty, reason = t.reason }).ToList();

            return new Dictionary<string, object?>
            {
                ["ok"] = completed,
                ["operation"] = operation,
                ["swings"] = _toolAreaTotalSwings,
                ["patches"] = patches,
                ["still_missing"] = stillList,
                ["result"] = result
            };
        }

        return calcResult;
    }

    /// <summary>蓄力锚点生成（till/water 共用，补漏轮次也用它）。
    /// 面向下蓄力：toolW=垂直宽, toolH=面向距离（实测 1→3距离, 2→5, 4→6距离×3宽）。
    /// 锚点站耕地外上方（ay = tile_y - 1），charge 释放时覆盖 forward 范围。</summary>
    private static List<Dictionary<string, object?>> BuildToolAreaCommands(
        List<(int tx, int ty)> targetTiles, int toolW, int toolH, int chargeFrames, int upgradeLevel)
    {
        int minX = targetTiles.Min(t => t.tx), maxX = targetTiles.Max(t => t.tx);
        int minY = targetTiles.Min(t => t.ty), maxY = targetTiles.Max(t => t.ty);
        int nx = (int)Math.Ceiling((double)(maxX - minX + 1) / toolW);
        int ny = (int)Math.Ceiling((double)(maxY - minY + 1) / toolH);
        var commands = new List<Dictionary<string, object?>>();
        for (int row = 0; row < ny; row++)
        {
            int start = row % 2 == 0 ? 0 : nx - 1, end = row % 2 == 0 ? nx - 1 : 0, step = row % 2 == 0 ? 1 : -1;
            for (int col = start; ; col += step)
            {
                int ax = Math.Clamp(minX + col * toolW + toolW / 2, minX, maxX);
                int ay = Math.Max(minY + row * toolH - 1, minY - 1);
                commands.Add(new Dictionary<string, object?> { ["action"] = "move", ["x"] = ax, ["y"] = ay });
                commands.Add(new Dictionary<string, object?> { ["action"] = "face", ["direction"] = 2 });
                // 逐锚点验证用（2026-08-15 恒）：该锚点蓄力/单格覆盖的格（toolW 宽 × toolH 高，面向下）
                var anchorTargets = new List<(int tx, int ty)>();
                int halfW2 = (toolW - 1) / 2;
                for (int dxx = -halfW2; dxx <= halfW2; dxx++)
                    for (int dyy = 1; dyy <= toolH; dyy++)
                        anchorTargets.Add((ax + dxx, ay + dyy));
                if (chargeFrames > 0)
                    commands.Add(new Dictionary<string, object?> { ["action"] = "charge", ["frames"] = chargeFrames, ["power"] = upgradeLevel, ["targets"] = anchorTargets });
                else
                    commands.Add(new Dictionary<string, object?> { ["action"] = "use", ["targets"] = anchorTargets });
                if (col == end) break;
            }
        }
        return commands;
    }

    /// <summary>主线程自检漏格（till: 应锄可锄但没成 HoeDirt；water: 有作物未浇）。
    /// 蓄力补漏的输入——只报漏，不改地块。reason=四向被什么挡（2026-08-15 恒：报错原因）。
    /// </summary>
    private List<(int tx, int ty, string reason)> FindMissingToolAreaTiles(string operation)
    {
        var tcs = new TaskCompletionSource<List<(int tx, int ty, string reason)>>();
        EnqueueMainThread(() =>
        {
            try
            {
                var loc = Game1.currentLocation;
                var missing = new List<(int tx, int ty, string reason)>();
                foreach (var (tx, ty) in _toolAreaTargets)
                {
                    var vec = new Vector2(tx, ty);
                    bool isMissing = operation == "till"
                        ? (!loc.terrainFeatures.ContainsKey(vec) && !loc.objects.ContainsKey(vec)
                           && loc.doesTileHaveProperty(tx, ty, "Diggable", "Back") != null)
                        : (loc.terrainFeatures.TryGetValue(vec, out var tf) && tf is HoeDirt dirt
                           && dirt.crop != null && dirt.state.Value == 0);
                    if (isMissing)
                        missing.Add((tx, ty, MissingReason(loc, tx, ty)));
                }
                tcs.SetResult(missing);
            }
            catch (Exception) { tcs.SetResult(new List<(int, int, string)>()); }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>漏格报错原因：四向邻格被什么挡（object/terrain/地图外）。</summary>
    private static string MissingReason(GameLocation loc, int tx, int ty)
    {
        var parts = new List<string>();
        var dirs = new (int dx, int dy, string name)[] { (0, -1, "上"), (0, 1, "下"), (-1, 0, "左"), (1, 0, "右") };
        foreach (var (dx, dy, name) in dirs)
        {
            var v = new Vector2(tx + dx, ty + dy);
            if (!loc.isTileOnMap(v))
                parts.Add($"{name}地图外");
            else if (loc.objects.TryGetValue(v, out var obj))
                parts.Add($"{name}{obj.DisplayName}");
            else if (loc.terrainFeatures.TryGetValue(v, out var tf))
                parts.Add($"{name}{tf.GetType().Name}");
        }
        return parts.Count > 0 ? string.Join("；", parts) : "四向空旷但补不上";
    }

    /// <summary>逐锚点验证覆盖格是否"落地"（till=成 HoeDirt；water=有作物则已浇/无作物免）。漏的当场补漏。</summary>
    private void VerifyToolAreaAnchor(Dictionary<string, object?> cmd)
    {
        try
        {
            if (!cmd.TryGetValue("targets", out var tval) || tval is not List<(int tx, int ty)> targets) return;
            var missed = new List<(int tx, int ty)>();
            foreach (var (tx, ty) in targets)
            {
                if (!TileDone(_toolAreaOperation, tx, ty))
                    missed.Add((tx, ty));
            }
            if (missed.Count > 0)
                PatchMissingOnMain(_toolAreaOperation, missed);
        }
        catch { }
    }

    private static bool TileDone(string operation, int tx, int ty)
    {
        var loc = Game1.currentLocation;
        var vec = new Vector2(tx, ty);
        if (operation == "water")
            return loc.terrainFeatures.TryGetValue(vec, out var tf) && tf is HoeDirt d && (d.crop == null || d.state.Value == 1);
        return loc.terrainFeatures.ContainsKey(vec) && loc.terrainFeatures[vec] is HoeDirt;
    }

    /// <summary>主线程直接补漏（取余补站位逐格）：对每个漏格，position 到候选站位 + face + 直接 DoFunction，
    /// 逐个方向尝试直到补上。不走命令队列、不等待蓄力——避开 charge 蓄力等待期间位置漂移导致 fallback 错位的问题
    /// （实测 2026-08-15：tool_area charge 对 (73,20-22) 列失效，手动 position+挥壶能浇上）。
    /// 返回本次补上的格数。</summary>
    private int PatchMissingOnMain(string operation, List<(int tx, int ty)> missing)
    {
        var farmer = Game1.player;
        var loc = farmer?.currentLocation;
        if (farmer == null || loc == null) return 0;
        string requiredTool = operation == "water" ? "Watering Can" : "Hoe";
        Tool? tool = farmer.CurrentTool;
        if (tool == null || !tool.Name.Contains(requiredTool))
        {
            var found = farmer.Items.OfType<Tool>().FirstOrDefault(t => t.Name.Contains(requiredTool));
            if (found == null) return 0;
            farmer.CurrentToolIndex = farmer.Items.IndexOf(found);
            tool = found;
        }
        // 补漏前灌满水壶（water 操作；直接改地块不做真浇水）
        if (operation == "water" && tool is WateringCan wc)
            wc.WaterLeft = wc.waterCanMax;
        // 候选站位方向（相对漏格）：上/下/左/右，对应 face 2/0/1/3
        var cands = new (int dx, int dy, int face)[] { (0, -1, 2), (0, 1, 0), (-1, 0, 1), (1, 0, 3) };
        int fixedCount = 0;
        foreach (var (tx, ty) in missing)
        {
            bool patched = false;
            foreach (var (dx, dy, face) in cands)
            {
                int sx = tx + dx, sy = ty + dy;
                farmer.Position = new Vector2(sx, sy) * Game1.tileSize;
                farmer.FacingDirection = face;
                var facingTile = GetFacingTile(farmer);
                int px = (int)facingTile.X * 64 + 32;
                int py = (int)facingTile.Y * 64 + 32;
                try
                {
                    if (operation == "water" && tool is WateringCan wc2)
                        wc2.DoFunction(loc, px, py, _toolAreaUpgradeLevel, farmer);
                    else if (operation == "till" && tool is Hoe hoe)
                        hoe.DoFunction(loc, px, py, _toolAreaUpgradeLevel, farmer);
                }
                catch (Exception) { }
                var vec = new Vector2(tx, ty);
                if (operation == "till")
                    patched = loc.terrainFeatures.ContainsKey(vec) && loc.terrainFeatures[vec] is HoeDirt;
                else
                    patched = loc.terrainFeatures.TryGetValue(vec, out var tf) && tf is HoeDirt dirt && dirt.state.Value == 1;
                if (patched) { fixedCount++; break; }
            }
            // 4 方向都补不上：位置复位（避免留下卡位）
            if (!patched)
            {
                try { farmer.Position = new Vector2(tx, ty - 1) * Game1.tileSize; } catch { }
            }
        }
        return fixedCount;
    }

    /// <summary>
    /// ── 命令队列参数读取：兼容 C# 直接构造的 Dictionary（tool_area）和 HTTP 来的 JsonElement ──
    private static string CmdString(Dictionary<string, object?> cmd, string key, string def = "")
    {
        if (!cmd.TryGetValue(key, out var val) || val == null) return def;
        if (val is JsonElement je)
            return je.ValueKind == JsonValueKind.String ? je.GetString() ?? def : je.ToString() ?? def;
        return val.ToString() ?? def;
    }

    private static int CmdInt(Dictionary<string, object?> cmd, string key, int def = 0)
    {
        if (!cmd.TryGetValue(key, out var val) || val == null) return def;
        if (val is JsonElement je)
        {
            if (je.ValueKind == JsonValueKind.Number) return je.GetInt32();
            return int.TryParse(je.ToString(), out var n) ? n : def;
        }
        if (val is int i) return i;
        return int.TryParse(val.ToString(), out var n2) ? n2 : def;
    }

    /// Calculate tiles affected by a tool swing at (fx, fy) facing direction with given power.
    /// Power: 0=1 tile, 1=3 line, 2=5 line, 3=3x3 area, 4=6x3 area
    /// </summary>
    private static List<(int, int)> GetToolAffectedTiles(int fx, int fy, int facing, int power)
    {
        var tiles = new List<(int, int)>();
        int fdx = facing switch { 1 => 1, 3 => -1, _ => 0 };
        int fdy = facing switch { 0 => -1, 2 => 1, _ => 0 };
        int pdx = facing switch { 0 => 1, 2 => -1, 1 => 0, 3 => 0, _ => 0 };
        int pdy = facing switch { 0 => 0, 2 => 0, 1 => 1, 3 => -1, _ => 0 };

        // w=垂直宽(perp), h=面向距离(forward)。实测2026-08-02：1→3距离, 2→5距离, 4→6距离×3宽
        int w = power switch { 0 => 1, 1 => 1, 2 => 1, 3 => 3, 4 => 3, _ => 1 };
        int h = power switch { 0 => 1, 1 => 3, 2 => 5, 3 => 3, 4 => 6, _ => 1 };
        int halfW = (w - 1) / 2;

        for (int hi = 1; hi <= h; hi++)
            for (int wi = -halfW; wi <= w / 2; wi++)
                tiles.Add((fx + fdx * hi + pdx * wi, fy + fdy * hi + pdy * wi));

        return tiles;
    }

    private object HandleMuseumDonate()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
	                var farmer = Game1.player;
	                var museum = Game1.getLocationFromName("ArchaeologyHouse");
	                if (museum == null)
	                {
	                    tcs.SetResult(new { ok = false, error = "ArchaeologyHouse not found" });
	                    return;
	                }

	                		                // 获取合法空展位: 用 LibraryMuseum.getFreeDonationSpot()
		                var displaySlots = new List<(int x, int y)>();
		                try
		                {
		                    var libMuseum = museum as StardewValley.Locations.LibraryMuseum;
		                    if (libMuseum != null)
		                    {
		                        while (true)
		                        {
		                            var spot = libMuseum.getFreeDonationSpot();
		                            if (spot == default || (spot.X == 0 && spot.Y == 0) || displaySlots.Contains(((int)spot.X, (int)spot.Y)))
		                                break;
		                            displaySlots.Add(((int)spot.X, (int)spot.Y));
		                            var vKey = new Vector2(spot.X, spot.Y);
		                            Game1.netWorldState.Value.MuseumPieces[vKey] = "_diag_";
		                        }
		                        foreach (var (sx, sy) in displaySlots)
		                        {
		                            var vKey = new Vector2(sx, sy);
		                            if (Game1.netWorldState.Value.MuseumPieces.TryGetValue(vKey, out var val) && val == "_diag_")
		                                Game1.netWorldState.Value.MuseumPieces.Remove(vKey);
		                        }
		                    }
		                }
		                catch { }
		                // 兜底: 手动扫 Buildings 图层 tile index 173
		                if (displaySlots.Count == 0)
		                {
		                    try
		                    {
		                        var bLayer = museum.Map.GetLayer("Buildings");
		                        if (bLayer != null)
		                        {
		                            for (int x = 0; x < bLayer.LayerWidth; x++)
		                                for (int y = 0; y < bLayer.LayerHeight; y++)
		                                    if (bLayer.Tiles[x, y]?.TileIndex == 173)
		                                        displaySlots.Add((x, y));
		                        }
		                    }
		                    catch { }
		                }
		                // 兜底2: 已知标准展位
		                if (displaySlots.Count == 0)
		                {
		                    var known = new (int, int)[] {
		                        (26,11),(27,11),(28,11),(29,11),(30,11),(31,11),(32,11),(33,11),(34,11),(35,11),
		                        (27,8),(28,8),(29,8),(30,8),(31,8),(32,8),(33,8),(34,8),(35,8),
		                        (27,6),(28,6),(29,6),(30,6),(31,6),(32,6),(33,6),(34,6),(35,6),
		                        (27,5),(28,5),(29,5),(30,5),(31,5),(32,5),(33,5),(34,5),(35,5),
		                        (27,4),(28,4),(29,4),(30,4),(31,4),(32,4),(33,4),(34,4),(35,4),
		                    };
		                    displaySlots.AddRange(known);
		                }
		                                // 找出空位
                var emptySlots = displaySlots
                    .Where(s => !farmer.archaeologyFound.Values.Any(v => v[0] == s.x && v[1] == s.y))
                    .ToList();

                var donated = new List<object>();
                for (int i = 0; i < farmer.Items.Count && emptySlots.Count > 0; i++)
                {
                    if (farmer.Items[i] is StardewValley.Object obj)
                    {
                        // 用 LibraryMuseum.isItemSuitableForDonation 判断（比 category 过滤准）
                        bool suitable = false;
                        try
                        {
                            var libMuseum = museum as StardewValley.Locations.LibraryMuseum;
                            if (libMuseum != null)
                                suitable = libMuseum.isItemSuitableForDonation(obj);
                        }
                        catch { }
                        if (!suitable)
                        {
                            // 兜底：按 category 过滤（矿物 -12 古物 -23 宝石 -26）
                            if (obj.Category != -12 && obj.Category != -23 && obj.Category != -26)
                            {
                                // 再加特例：矮人小工具等 cat=0 的可捐物品
                                string rawId = obj.ItemId ?? "";
                                string rawQid = obj.QualifiedItemId ?? "";
                                string rawBare = rawId.Contains(")") ? rawId.Split(')')[1] : rawId;
                                if (string.IsNullOrEmpty(rawBare)) rawBare = rawQid.Contains(")") ? rawQid.Split(')')[1] : rawQid;
                                // Dwarf Gadget
                                if (rawBare != "326" && rawBare != "(O)326")
                                    continue;
                            }
                        }

                        // 兼容多种 ID 格式：ItemId / QualifiedItemId / 裸 ID
                        string id = obj.ItemId ?? "";
                        string qid = obj.QualifiedItemId ?? "";
                        string bareId = id.Contains(")") ? id.Split(')')[1] : id;
                        if (string.IsNullOrEmpty(bareId)) bareId = qid.Contains(")") ? qid.Split(')')[1] : qid;

                        // 检查是否已捐（同时查裸ID和全ID）
                        bool alreadyDonated = farmer.archaeologyFound.ContainsKey(bareId)
                            || farmer.archaeologyFound.ContainsKey(id)
                            || farmer.archaeologyFound.ContainsKey(qid);
                        if (alreadyDonated) continue;

                        var slot = emptySlots[0];
                        emptySlots.RemoveAt(0);
                        farmer.archaeologyFound[bareId] = new int[] { slot.x, slot.y };
                        // 同步到博物馆展品数据
                        try {
                            var vec2 = new Vector2(slot.x, slot.y);
                            var np = Game1.netWorldState.Value.MuseumPieces;
                            if (!np.ContainsKey(vec2))
                                np.Add(vec2, bareId);
                        } catch { }
                        donated.Add(new { item = obj.Name, id = bareId, tileX = slot.x, tileY = slot.y,
                            itemId = id, qualifiedId = qid });
                        obj.Stack--;
                        if (obj.Stack <= 0) farmer.Items[i] = null;
                    }
                }

                int totalDonated = farmer.archaeologyFound.Keys.Count();
                int remaining = emptySlots.Count;
                if (donated.Count > 0)
                    tcs.SetResult(new { ok = true, donated, totalDonated, remainingSlots = remaining });
                else
                    tcs.SetResult(new { ok = false, error = "No new items to donate or no empty slots" });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    private object HandleMachines()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var loc = Game1.player.currentLocation;
                var machines = new List<object>();
                ScanLocationMachines(loc, loc.Name, machines, BuildBuildingMap());

                tcs.SetResult(new
                {
                    ok = true,
                    location = loc.Name,
                    count = machines.Count,
                    machines
                });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// 扫描一个地点的所有 bigCraftable 机器（Keg/Furnace/Dehydrator…），带 location 归属。
    /// 供 /machines（当前地点）和 /farm_report（全农场）复用。
    /// </summary>
    private static void ScanLocationMachines(GameLocation loc, string locationKey, List<object> machines,
        Dictionary<GameLocation, (int x, int y, int doorX, int doorY)>? buildingMap = null)
    {
        foreach (var pair in loc.objects.Pairs)
        {
            var obj = pair.Value;
            if (!obj.bigCraftable.Value) continue;

            string status;
            if (obj.readyForHarvest.Value)
                status = "ready";
            else if (obj.heldObject.Value != null || obj.MinutesUntilReady > 0)
                status = "processing";
            else
                status = "empty";

            var entry = new Dictionary<string, object?>
            {
                ["type"] = obj.Name,
                ["name"] = obj.Name,
                ["location"] = locationKey,
                ["x"] = (int)pair.Key.X,
                ["y"] = (int)pair.Key.Y,
                ["status"] = status,
                ["minutesLeft"] = obj.MinutesUntilReady
            };

            if (obj.heldObject.Value != null)
            {
                entry["heldItem"] = obj.heldObject.Value.Name;
                entry["heldItemId"] = obj.heldObject.Value.QualifiedItemId;
                entry["heldQuality"] = (obj.heldObject.Value as StardewValley.Object)?.Quality ?? 0;
            }

            // 建筑内部机器：附上建筑坐标+门坐标，供进门/精确定位
            // （多栋同名建筑如 Cabin 按 location 名字区分不开，只能按建筑坐标）。
            if (buildingMap != null && buildingMap.TryGetValue(loc, out var bp))
                entry["building"] = new { x = bp.x, y = bp.y, doorX = bp.doorX, doorY = bp.doorY };

            machines.Add(entry);
        }
    }

    /// <summary>建筑室内 location → (建筑在农场坐标, 门坐标)。门=进建筑用。</summary>
    private static Dictionary<GameLocation, (int x, int y, int doorX, int doorY)> BuildBuildingMap()
    {
        var map = new Dictionary<GameLocation, (int, int, int, int)>();
        var farm = Game1.getFarm();
        if (farm == null) return map;
        foreach (var b in farm.buildings)
        {
            if (b.indoors?.Value is GameLocation il)
            {
                int doorX = b.humanDoor.Value != Point.Zero ? b.tileX.Value + b.humanDoor.X : b.tileX.Value;
                int doorY = b.humanDoor.Value != Point.Zero ? b.tileY.Value + b.humanDoor.Y : b.tileY.Value;
                map[il] = (b.tileX.Value, b.tileY.Value, doorX, doorY);
            }
        }
        return map;
    }

    private object HandleAnimals()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var loc = Game1.player.currentLocation;
                var animals = new List<object>();

                IEnumerable<FarmAnimal>? animalList = null;
                if (loc is Farm farm)
                    animalList = farm.animals.Values;
                else if (loc is AnimalHouse ah)
                    animalList = ah.animals.Values;

                if (animalList != null)
                {
                    foreach (var a in animalList)
                    {
                        animals.Add(new
                        {
                            name = a.Name,
                            type = a.type.Value,
                            x = a.TilePoint.X,
                            y = a.TilePoint.Y,
                            wasPetToday = a.wasPet.Value,
                            friendship = a.friendshipTowardFarmer.Value,
                            happiness = a.happiness.Value,
                            fullness = a.fullness.Value,
                            age = a.age.Value,
                            home = a.home?.indoors.Value?.Name,
                            product = a.currentProduce.Value,
                            productReady = a.currentProduce.Value != null && a.currentProduce.Value != "-1"
                        });
                    }
                }

                tcs.SetResult(new
                {
                    ok = true,
                    location = loc.Name,
                    count = animals.Count,
                    animals
                });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// GET /farm_report — 全农场扫描（照抄游戏农场电脑的遍历范围）。
    /// 覆盖：室外 Farm + 所有建筑室内（棚/舍/小屋/温室）+ 独立 location（地窖 Cellar）。
    /// 返回作物聚合计数、动物逐只（带所属建筑）、机器逐台（带类型/位置/状态）。
    /// </summary>
    private object HandleFarmReport()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                // 遍历范围与 /machine_collect、/machine_load 共用（farm + 建筑室内 + 地窖）
                var farmLocs = ResolveLocations("");
                var buildingMap = BuildBuildingMap();

                var machines = new List<object>();
                var animals = new List<object>();
                var cropsByLoc = new List<Dictionary<string, object?>>();
                int cTotal = 0, cWatered = 0, cReady = 0;

                foreach (var loc in farmLocs)
                {
                    string locKey = loc.Name;
                    ScanLocationMachines(loc, locKey, machines, buildingMap);
                    ScanLocationCrops(loc, locKey, cropsByLoc, ref cTotal, ref cWatered, ref cReady);
                    foreach (var a in loc.animals.Values)
                    {
                        animals.Add(new
                        {
                            name = a.Name,
                            type = a.type.Value,
                            building = a.home?.indoors.Value?.Name ?? "Farm",
                            x = a.TilePoint.X,
                            y = a.TilePoint.Y,
                            wasPetToday = a.wasPet.Value,
                            productReady = a.currentProduce.Value != null && a.currentProduce.Value != "-1",
                            product = a.currentProduce.Value
                        });
                    }
                }

                tcs.SetResult(new
                {
                    ok = true,
                    season = Game1.currentSeason,
                    dayOfMonth = Game1.dayOfMonth,
                    year = Game1.year,
                    crops = new { total = cTotal, watered = cWatered, ready = cReady, byLocation = cropsByLoc },
                    animals = new { total = animals.Count, animals },
                    machines = new { total = machines.Count, machines }
                });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// POST /machine_collect  { location?, type?, limit? }
    /// 批量收机器产物（全农场或指定地点）：直接 addItemToInventory + 双清
    /// （heldObject + readyForHarvest 一起清，避免"鬼机器"）。
    /// 背包满即停手不丢物，跳过剩余待收只计数。返回每件产物的名字/ID/当前品质。
    /// </summary>
    private object HandleMachineCollect(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var location = GetParamOr(p, "location", "");
        var type = GetParamOr(p, "type", "");
        var limit = GetParamOr(p, "limit", 0);   // 0 = 不限

        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var farmer = Game1.player;
                var locs = ResolveLocations(location);

                int collected = 0, skippedFull = 0;
                var byType = new Dictionary<string, int>();
                var products = new List<object>();
                bool invFull = false;

                foreach (var (loc, tile, obj) in EnumerateMachines(locs, type))
                {
                    if (limit > 0 && collected >= limit)
                        break;
                    if (!obj.readyForHarvest.Value || obj.heldObject.Value == null)
                        continue;

                    if (invFull)      // 背包已满：剩下的待收只计数，不收
                    {
                        skippedFull++;
                        continue;
                    }

                    var held = obj.heldObject.Value;
                    var leftover = farmer.addItemToInventory(held);
                    if (leftover != null && leftover.Stack > 0)
                    {
                        invFull = true;
                        skippedFull++;
                        continue;   // 没塞进去，机器保持原样
                    }

                    obj.heldObject.Value = null;
                    obj.readyForHarvest.Value = false;
                    collected++;

                    byType[obj.Name] = byType.TryGetValue(obj.Name, out var c) ? c + 1 : 1;
                    products.Add(new
                    {
                        name = held.Name,
                        id = held.QualifiedItemId,
                        quality = (held as StardewValley.Object)?.Quality ?? 0,
                        location = loc.Name,
                        x = (int)tile.X,
                        y = (int)tile.Y
                    });
                }

                tcs.SetResult(new
                {
                    ok = true,
                    collected,
                    skippedFull,
                    byType,
                    products,
                    invFull
                });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// POST /machine_load  { itemId, location?, type?, count? }
    /// 批量往空机器放原料：把背包里匹配的物品设成手持位，再调机器自己的 checkForAction
    /// 让游戏算配方时间（Keg 酒6000/腌菜罐4000/Cask 自动设 daysToMature+agingRate…），
    /// 不手写配方表，副作用（音效/统计）也由游戏处理。
    /// 槽位消耗完自动切下一个有相同物品的槽位；不匹配的机器自动跳过。
    /// </summary>
    private object HandleMachineLoad(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var rawId = GetParam<string>(p, "itemId");
        var location = GetParamOr(p, "location", "");
        var type = GetParamOr(p, "type", "");
        var count = GetParamOr(p, "count", int.MaxValue);

        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var farmer = Game1.player;

                // 解析物品：先当 ID，再按名字在背包里找（Name 或中文 DisplayName）
                var qid = ResolveItemId(rawId, farmer);
                if (qid == null)
                {
                    tcs.SetResult(new { ok = false, error = $"找不到物品: {rawId}" });
                    return;
                }
                if (CountItem(farmer, qid) <= 0)
                {
                    tcs.SetResult(new { ok = false, error = $"背包里没有 {rawId}", available = 0 });
                    return;
                }

                var locs = ResolveLocations(location);
                int loaded = 0, emptySeen = 0, rejected = 0, noSlot = 0;

                foreach (var (loc, tile, obj) in EnumerateMachines(locs, type))
                {
                    if (count != int.MaxValue && loaded >= count)
                        break;
                    if (obj.heldObject.Value != null || obj.MinutesUntilReady > 0 || obj.readyForHarvest.Value)
                        continue;   // 只装空机器
                    emptySeen++;

                    int slot = FindItemSlot(farmer, qid);
                    if (slot < 0)
                    {
                        noSlot++;
                        break;      // 原料用完了
                    }
                    farmer.CurrentToolIndex = slot;

                    // ⚠️ checkForAction 要求操作角色和机器同地点（warp 换地点是异步的，
                    // 下个 tick 才生效），所以这里同步把角色挪到机器所在 location + 旁边。
                    RelocateActor(loc, tile);

                    bool acted;
                    try
                    {
                        // 用 /interact 同款路径 loc.checkAction(瓦片) —— 实测这个能触发机器交互；
                        // 直接 obj.checkForAction 会因地点/激活状态不满足而静默失败。
                        acted = loc.checkAction(new Location((int)tile.X, (int)tile.Y), Game1.viewport, farmer);
                    }
                    catch
                    {
                        acted = false;
                    }

                    if (acted && obj.heldObject.Value != null)
                        loaded++;
                    else
                        rejected++;
                }

                tcs.SetResult(new
                {
                    ok = true,
                    loaded,
                    emptySeen,
                    rejected,
                    noSlot,
                    item = rawId,
                    remaining = CountItem(farmer, qid)   // 背包里剩的
                });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>解析地点参数：指定名字 → 该地点；空 → 全农场（farm + 建筑室内 + 地窖）。</summary>
    private static List<GameLocation> ResolveLocations(string location)
    {
        if (!string.IsNullOrEmpty(location))
        {
            var loc = FindLocationByName(location);
            if (loc == null)
                throw new InvalidOperationException($"找不到地点: {location}");
            return new List<GameLocation> { loc };
        }

        var list = new List<GameLocation>();
        var seen = new HashSet<GameLocation>();
        var farm = Game1.getFarm();
        if (farm != null)
        {
            if (seen.Add(farm)) list.Add(farm);
            foreach (var b in farm.buildings)
            {
                if (b.indoors?.Value is GameLocation indoor && seen.Add(indoor))
                    list.Add(indoor);   // 棚/舍/小屋/温室
            }
        }
        foreach (var loc in Game1.locations)
        {
            if (loc is Cellar && seen.Add(loc))
                list.Add(loc);          // 地窖是独立 location，不在 farm.buildings
        }
        return list;
    }

    private static GameLocation? FindLocationByName(string name)
    {
        foreach (var loc in Game1.locations)
        {
            if (loc.Name.Equals(name, StringComparison.OrdinalIgnoreCase))
                return loc;
        }
        var farm = Game1.getFarm();
        if (farm != null)
        {
            foreach (var b in farm.buildings)
            {
                if (b.indoors?.Value is GameLocation indoor
                    && indoor.Name.Equals(name, StringComparison.OrdinalIgnoreCase))
                    return indoor;
            }
        }
        return Game1.getLocationFromName(name);
    }

    /// <summary>枚举机器：bigCraftable + 可选类型过滤。供 collect/load 复用。</summary>
    private static IEnumerable<(GameLocation loc, Vector2 tile, StardewValley.Object obj)> EnumerateMachines(
        IEnumerable<GameLocation> locs, string? type)
    {
        foreach (var loc in locs)
        {
            foreach (var pair in loc.objects.Pairs)
            {
                var obj = pair.Value;
                if (!obj.bigCraftable.Value) continue;
                if (!string.IsNullOrEmpty(type) && !obj.Name.Equals(type, StringComparison.OrdinalIgnoreCase))
                    continue;
                yield return (loc, pair.Key, obj);
            }
        }
    }

    /// <summary>解析要装入机器的物品：先当 ItemId，再按名字在背包里找，返回 QualifiedItemId。</summary>
    private static string? ResolveItemId(string rawId, Farmer farmer)
    {
        if (string.IsNullOrWhiteSpace(rawId)) return null;
        var trimmed = rawId.Trim();
        if (trimmed.StartsWith("("))
            return trimmed;
        var tryQ = ItemRegistry.QualifyItemId(trimmed);
        if (tryQ != null)
            return tryQ;
        foreach (var it in farmer.Items)
        {
            if (it == null) continue;
            if (it.Name.Equals(trimmed, StringComparison.OrdinalIgnoreCase)
                || it.DisplayName.Equals(trimmed, StringComparison.OrdinalIgnoreCase))
                return it.QualifiedItemId;
        }
        return null;
    }

    /// <summary>背包里某物品（QualifiedItemId）的总堆叠数。</summary>
    private static int CountItem(Farmer farmer, string qid)
    {
        int total = 0;
        foreach (var it in farmer.Items)
        {
            if (it != null && it.QualifiedItemId == qid)
                total += it.Stack;
        }
        return total;
    }

    /// <summary>找背包里含指定 QualifiedItemId 且有数量的第一个槽位，找不到返回 -1。</summary>
    private static int FindItemSlot(Farmer farmer, string qid)
    {
        for (int i = 0; i < farmer.Items.Count; i++)
        {
            var it = farmer.Items[i];
            if (it != null && it.QualifiedItemId == qid && it.Stack > 0)
                return i;
        }
        return -1;
    }

    /// <summary>
    /// 同步把操作角色挪到指定机器的所在地点 + 旁边格。
    /// 不走 Game1.warpFarmer（那是异步的，下个 tick 才换地点），直接设 currentLocation，
    /// 这样紧随其后的 checkForAction 能通过同地点检查。
    /// </summary>
    private static void RelocateActor(GameLocation loc, Vector2 tile)
    {
        var farmer = Game1.player;
        if (farmer.currentLocation != loc)
        {
            farmer.currentLocation = loc;
        }

        // 机器旁的空白格。skipCenter=true 绝不站到机器本格上
        // （Cask 可踩踏，不然会落到机器身上点不到它）。防 warp 进墙：找不到就不动。
        var walkable = FindWalkableTile(loc, (int)tile.X, (int)tile.Y, skipCenter: true);
        if (walkable != null)
        {
            farmer.Position = new Vector2(walkable.Value.x, walkable.Value.y) * Game1.tileSize;
        }
    }

    /// <summary>
    /// 扫一个地点的 HoeDirt 作物，统计 total/watered/ready，并追加到 byLocation。
    /// 逻辑同 /surroundings 的作物读取（state==1→watered，readyForHarvest→ready）。
    /// </summary>
    private static void ScanLocationCrops(GameLocation loc, string locationKey,
        List<Dictionary<string, object?>> byLocation, ref int total, ref int watered, ref int ready)
    {
        int lTotal = 0, lWatered = 0, lReady = 0;
        foreach (var pair in loc.terrainFeatures.Pairs)
        {
            if (pair.Value is HoeDirt dirt && dirt.crop != null)
            {
                lTotal++;
                if (dirt.state.Value == 1) lWatered++;
                if (dirt.readyForHarvest()) lReady++;
            }
        }
        total += lTotal; watered += lWatered; ready += lReady;
        if (lTotal > 0)
            byLocation.Add(new Dictionary<string, object?>
            {
                ["location"] = locationKey,
                ["total"] = lTotal,
                ["watered"] = lWatered,
                ["ready"] = lReady
            });
    }

    private object HandleScan()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var loc = Game1.currentLocation;
                var actions = new List<object>();
                for (int x = 0; x < loc.Map.Layers[0].LayerWidth; x++)
                {
                    for (int y = 0; y < loc.Map.Layers[0].LayerHeight; y++)
                    {
                        string? action = loc.doesTileHaveProperty(x, y, "Action", "Buildings");
                        if (action != null)
                            actions.Add(new { x, y, action });
                    }
                }
                tcs.SetResult(new { ok = true, location = loc.Name, count = actions.Count, actions });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    private object HandlePetBowl()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var farm = Game1.getFarm();
                if (farm == null)
                {
                    tcs.SetResult(new { ok = false, error = "Not on a farm" });
                    return;
                }

                Point? bowlPos = null;
                string method = "none";
                StardewValley.Buildings.PetBowl? bowlBuilding = null;

                // Method 0 (best, SDV 1.6): Pet Bowl is a Building — 扫 farm.buildings 找 PetBowl 类型
                // ⚠️ 2026-08-16 恒实测：1.6 宠物碗是 PetBowl 建筑（本档 @(53,7) 2x2，门(52,6)），
                //    farm.petBowl 字段/旧 tile 扫描会误报（如 (7,6)）。建筑扫描最准。
                try
                {
                    foreach (var pb in farm.buildings)
                    {
                        if (pb is StardewValley.Buildings.PetBowl pbw)
                        {
                            bowlPos = new Point(pb.tileX.Value, pb.tileY.Value);
                            bowlBuilding = pbw;
                            method = "building:PetBowl";
                            break;
                        }
                    }
                }
                catch { }

                // Method 1: Try to read farm.petBowl field directly (老档/非1.6兜底)
                if (bowlPos == null)
                try
                {
                    var petBowlField = farm.GetType().GetField("petBowl",
                        BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                    if (petBowlField != null && petBowlField.GetValue(farm) is Point pb)
                    {
                        bowlPos = pb;
                        method = "field:petBowl";
                    }
                }
                catch { }

                // Method 2: Scan Buildings layer for pet bowl tile or action
                if (bowlPos == null)
                {
                    var buildingsLayer = farm.Map.GetLayer("Buildings");
                    for (int x = 0; x < buildingsLayer.LayerWidth && bowlPos == null; x++)
                    {
                        for (int y = 0; y < buildingsLayer.LayerHeight && bowlPos == null; y++)
                        {
                            var tile = buildingsLayer.Tiles[x, y];
                            if (tile == null) continue;

                            // Check Action property
                            string? action = farm.doesTileHaveProperty(x, y, "Action", "Buildings");
                            if (action == "PetBowl")
                            {
                                bowlPos = new Point(x, y);
                                method = "action:PetBowl";
                                break;
                            }

                            // Try a broad range of tile indices used by pet bowls
                            int idx = tile.TileIndex;
                            if (idx >= 1930 && idx <= 1960)
                            {
                                bowlPos = new Point(x, y);
                                method = $"tileIndex:{idx}";
                            }
                        }
                    }
                }

                // Method 3: Find the pet NPC and use its position as a hint
                if (bowlPos == null)
                {
                    foreach (var npc in farm.characters)
                    {
                        if (npc is Pet p)
                        {
                            // Pet bowl is typically 1-2 tiles below where the pet stands
                            bowlPos = new Point((int)p.TilePoint.X, (int)p.TilePoint.Y + 1);
                            method = "petPosition";
                            break;
                        }
                    }
                }

                // Method 4: Scan and dump tile indices near the farmhouse for debugging
                if (bowlPos == null)
                {
                    var bldg = farm.Map.GetLayer("Buildings");
                    var house = farm.GetMainFarmHouse();
                    if (house != null)
                    {
                        int hx = house.tileX.Value;
                        int hy = house.tileY.Value;
                        for (int x = Math.Max(0, hx - 10); x < Math.Min(bldg.LayerWidth, hx + 15); x++)
                        {
                            for (int y = Math.Max(0, hy - 10); y < Math.Min(bldg.LayerHeight, hy + 15); y++)
                            {
                                var tile = bldg.Tiles[x, y];
                                if (tile != null)
                                {
                                    int idx = tile.TileIndex;
                                    if ((idx >= 1880 && idx <= 2000) || idx == 2125 || idx == 2126)
                                    {
                                        bowlPos = new Point(x, y);
                                        method = $"tileIndex:{idx}";
                                        break;
                                    }
                                }
                            }
                            if (bowlPos.HasValue) break;
                        }
                    }
                }

                // Method 5: Last resort - dump all tile indices
                if (bowlPos == null)
                {
                    var bldg = farm.Map.GetLayer("Buildings");
                    HashSet<int> indices = new();
                    for (int x = 0; x < bldg.LayerWidth; x++)
                        for (int y = 0; y < bldg.LayerHeight; y++)
                            if (bldg.Tiles[x, y] != null)
                                indices.Add(bldg.Tiles[x, y].TileIndex);
                    tcs.SetResult(new { ok = false, error = "Pet bowl not found",
                        visibleIndices = string.Join(",", indices.OrderBy(i => i)),
                        farmType = farm.Name ?? "Farm" });
                    return;
                }

                if (bowlPos.HasValue)
                {
                    // Also check if there's a pet in the farm
                    NPC? pet = null;
                    foreach (var npc in farm.characters)
                    {
                        if (npc is Pet petNpc)
                        {
                            pet = petNpc;
                            break;
                        }
                    }

                    // Check if the bowl has been watered today
                    bool bowlWatered = false;
                    string waterCheckMethod = "unknown";
                    // ⚠️ 2026-08-16 1.6：宠物碗是 PetBowl 建筑，watered 在 building.watered (NetBool)
                    if (bowlBuilding != null)
                    {
                        try { bowlWatered = bowlBuilding.watered.Value; waterCheckMethod = "building.watered"; }
                        catch { }
                    }
                    if (waterCheckMethod == "unknown")
                    try
                    {
                        // Try direct field access (works in most Stardew versions)
                        var field = farm.GetType().GetField("petBowlWatered",
                            BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                        if (field != null)
                        {
                            bowlWatered = (bool)(field.GetValue(farm) ?? false);
                            waterCheckMethod = "field";
                        }
                    }
                    catch { /* field access failed, try another way */ }

                    if (waterCheckMethod == "unknown")
                    {
                        // Fallback: check if the bowl tile has a "Watered" property
                        try
                        {
                            foreach (var npc in farm.characters)
                            {
                                if (npc is Pet petNpc)
                                {
                                    // Pet's "wasPetToday" or similar could indicate bowl watered
                                    var petField = petNpc.GetType().GetField("wasPetToday",
                                        BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                                    if (petField != null)
                                    {
                                        bowlWatered = false; // couldn't determine, assume false
                                        waterCheckMethod = "unknown";
                                    }
                                    break;
                                }
                            }
                        }
                        catch { }
                    }

                    tcs.SetResult(new
                    {
                        ok = true,
                        bowl = new { x = bowlPos.Value.X, y = bowlPos.Value.Y },
                        // ⚠️ 2026-08-16 门坐标 = 浇水站位参考（本档碗(53,7)，门(52,6)，站门+1朝右浇）
                        door = bowlBuilding != null
                            ? new { x = bowlBuilding.tileX.Value + bowlBuilding.humanDoor.X,
                                    y = bowlBuilding.tileY.Value + bowlBuilding.humanDoor.Y }
                            : (object)new { x = bowlPos.Value.X, y = bowlPos.Value.Y },
                        bowlWatered,
                        method,
                        pet = pet != null ? new { name = pet.Name, x = (int)pet.TilePoint.X, y = (int)pet.TilePoint.Y } : null
                    });
                }
                else
                {
                    // Fallback: check known farm type coordinates
                    tcs.SetResult(new { ok = false, error = "Pet bowl not found on map", farmType = farm.Name });
                }
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    private object HandlePetAll()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var farm = Game1.getFarm();
                if (farm == null)
                {
                    tcs.SetResult(new { ok = false, error = "Not on a farm" });
                    return;
                }

                var petted = new List<object>();
                var errors = new List<string>();

                // ── 摸宠物（猫/狗） ──
                foreach (var npc in farm.characters)
                {
                    if (npc is Pet petNpc)
                    {
                        try
                        {
                            // 方法1: 直接调用 pet() 方法（如果有）
                            var petMethod = petNpc.GetType().GetMethod("pet",
                                BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                            if (petMethod != null)
                            {
                                petMethod.Invoke(petNpc, petMethod.GetParameters().Length == 1
                                    ? new object[] { Game1.player } : null);
                                petted.Add(new { type = "pet", name = petNpc.Name, method = "pet()" });
                            }
                            else
                            {
                                // 方法2: 设置 wasPetToday 字段
                                bool flagged = false;
                                foreach (var fname in new[] { "wasPetToday", "wasPet", "petted" })
                                {
                                    var f = petNpc.GetType().GetField(fname,
                                        BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                                    if (f != null && f.FieldType == typeof(bool))
                                    {
                                        f.SetValue(petNpc, true);
                                        petted.Add(new { type = "pet", name = petNpc.Name, method = $"field:{fname}" });
                                        flagged = true;
                                        break;
                                    }
                                }
                                if (!flagged)
                                {
                                    // 方法3: 模拟 checkAction
                                    petNpc.checkAction(Game1.player, Game1.currentLocation);
                                    petted.Add(new { type = "pet", name = petNpc.Name, method = "checkAction" });
                                }
                            }
                        }
                        catch (Exception ex)
                        {
                            errors.Add($"pet '{petNpc.Name}': {ex.Message}");
                        }
                    }
                }

                // ── 摸农场动物（鸡牛羊猪……） ──
                foreach (var building in farm.buildings)
                {
                    // SDV 1.6: use AnimalHouse instead of deprecated Barn/Coop
                    var indoors = building.indoors?.Value;
                    if (indoors == null) continue;
                    if (!(indoors is AnimalHouse)) continue;

                        // Get animals from the building's indoor location
                        var animalsField = indoors.GetType().GetField("animals",
                            BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                        if (animalsField?.GetValue(indoors) is IEnumerable<FarmAnimal> animalList)
                        {
                            foreach (var animal in animalList)
                            {
                                try
                                {
                                    bool flagged = false;
                                    foreach (var fname in new[] { "wasPet", "wasPetToday", "petted" })
                                    {
                                        var f = animal.GetType().GetField(fname,
                                            BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                                        if (f != null && f.FieldType == typeof(bool))
                                        {
                                            f.SetValue(animal, true);
                                            petted.Add(new { type = "farmAnimal", name = animal.Name ?? animal.displayName, method = $"field:{fname}" });
                                            flagged = true;
                                            break;
                                        }
                                    }
                                    if (!flagged)
                                    {
                                        var m = animal.GetType().GetMethod("pet",
                                            BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                                        if (m != null)
                                        {
                                            m.Invoke(animal, m.GetParameters().Length == 1
                                                ? new object[] { Game1.player } : null);
                                            petted.Add(new { type = "farmAnimal", name = animal.Name ?? animal.displayName, method = "pet()" });
                                            flagged = true;
                                        }
                                    }
                                    if (!flagged)
                                        errors.Add($"animal '{animal.displayName}': no known field/method");
                                }
                                catch (Exception ex)
                                {
                                    errors.Add($"animal '{animal.displayName}': {ex.Message}");
                                }
                            }
                    }
                }

                tcs.SetResult(new
                {
                    ok = true,
                    petted = petted.Count,
                    details = petted,
                    errors = errors.Count > 0 ? errors : null
                });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    private object HandleWaterBowl()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var farm = Game1.getFarm();
                if (farm == null)
                {
                    tcs.SetResult(new { ok = false, error = "Not on a farm" });
                    return;
                }

                bool set = false;
                string method = "unknown";
                List<string> tried = new();

                // Method 1: Try many possible field names
                string[] fieldNames = {
                    "petBowlWatered", "petBowlWateredToday", "wateredPetBowl",
                    "petWaterBowl", "petWaterBowlWatered",
                    "petBowlFilled", "wasPetBowlWatered"
                };
                foreach (var fname in fieldNames)
                {
                    tried.Add(fname);
                    var f = farm.GetType().GetField(fname,
                        BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                    if (f != null)
                    {
                        if (f.FieldType == typeof(bool))
                        { f.SetValue(farm, true); set = true; method = $"field:{fname}"; break; }
                        if (f.FieldType.Name == "NetBool" || f.FieldType.Name == "NetBoolDelta")
                        {
                            var val = f.GetValue(farm);
                            val?.GetType().GetMethod("Set")?.Invoke(val, new object[] { true });
                            set = true; method = $"netfield:{fname}"; break;
                        }
                    }
                }

                // Method 2: Scan all pet/bowl related fields
                if (!set)
                {
                    foreach (var f in farm.GetType().GetFields(
                        BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance))
                    {
                        if (!f.Name.ToLower().Contains("pet") && !f.Name.ToLower().Contains("bowl"))
                            continue;
                        tried.Add($"(scan){f.Name}:{f.FieldType.Name}");
                        if (f.FieldType == typeof(bool))
                        { f.SetValue(farm, true); set = true; method = $"scan:{f.Name}"; break; }
                    }
                }

                // Method 3: Simulate right-click at bowl position
                if (!set)
                {
                    try
                    {
                        var bpField = farm.GetType().GetField("petBowl",
                            BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                        if (bpField?.GetValue(farm) is Point bp)
                        {
                            var wc = Game1.player.Items.OfType<WateringCan>().FirstOrDefault();
                            if (wc != null && wc.WaterLeft > 0)
                            {
                                Game1.player.CurrentTool = wc;
                                int px = bp.X * 64 + 32;
                                int py = bp.Y * 64 + 32;
                                if (farm.checkAction(new Location(px, py), Game1.viewport, Game1.player))
                                { set = true; method = "simulateCheckAction"; }
                            }
                        }
                    }
                    catch { }
                }

                if (!set)
                {
                    tcs.SetResult(new { ok = false, error = "Could not water bowl",
                        attempted = tried.ToArray() });
                    return;
                }

                tcs.SetResult(new { ok = true, watered = true, method });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    // ═══════════════════════════════════════════════════════════════
    //  🏗️ 建筑/干草/精通（2026-08-06 新增）
    // ═══════════════════════════════════════════════════════════════

    /// <summary>
    /// GET /silo — 筒仓干草检测。
    /// 返回筒仓数、已存干草、容量、空余。（每个筒仓容量 240）
    /// </summary>
    private object HandleSilo()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var farm = Game1.getFarm();
                int silos = farm.buildings.Count(b => b.buildingType.Value.Contains("Silo"));
                int hay = farm.piecesOfHay.Value;
                int capacity = silos * 240;
                tcs.SetResult(new
                {
                    ok = true,
                    silos,
                    hay,
                    capacity,
                    room = Math.Max(0, capacity - hay),
                    full = silos > 0 && hay >= capacity,
                    noSilo = silos == 0
                });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// GET /buffs — 当前生效的 buff 列表（右上角那些，含祝福/食物效果），含剩余毫秒。
    /// 吃东西逻辑靠它：知道 buff 还剩多久，快过期就补吃。
    /// </summary>
    private object HandleBuffs()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var list = new List<object>();
                var flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static;

                // 反射找 buff 容器。
                // 1.6 实测：buffsDisplay.buffs 是私有 Dictionary（真正的 buff 列表）；
                // Farmer.buffs 是 BuffManager（不是字典，会误导）→ 先查 buffsDisplay！
                object? container = null;
                if (Game1.buffsDisplay != null)
                {
                    foreach (var name in new[] { "buffs", "Buff" })
                    {
                        try
                        {
                            var prop = Game1.buffsDisplay.GetType().GetProperty(name, flags);
                            if (prop != null) { container = prop.GetValue(Game1.buffsDisplay); if (container != null) break; }
                        }
                        catch { }
                        try
                        {
                            var field = Game1.buffsDisplay.GetType().GetField(name, flags);
                            if (field != null) { container = field.GetValue(Game1.buffsDisplay); if (container != null) break; }
                        }
                        catch { }
                    }
                }
                // 兜底：Farmer.buffs (BuffManager) → 调 GetAppliedBuffs()
                if (container == null)
                {
                    try
                    {
                        var bmField = typeof(Farmer).GetField("buffs", flags);
                        var bm = bmField?.GetValue(Game1.player);
                        if (bm != null)
                        {
                            foreach (var mn in new[] { "GetAppliedBuffs", "getAppliedBuffs", "GetBuffs" })
                            {
                                var m = bm.GetType().GetMethod(mn, flags);
                                if (m != null) { container = m.Invoke(bm, null); break; }
                            }
                        }
                    }
                    catch { }
                }

                // 诊断：列出 player 和 buffsDisplay 上所有集合字段（找祝福存哪）
                var diagnostic = new List<object>();
                foreach (var src in new[] { (object?)Game1.player, Game1.buffsDisplay })
                {
                    if (src == null) continue;
                    foreach (var f in src.GetType().GetFields(flags))
                    {
                        object? v = null;
                        try { v = f.GetValue(src); } catch { }
                        if (v is System.Collections.IEnumerable en2)
                        {
                            int c = 0;
                            try { foreach (var _ in en2) { c++; if (c > 20) break; } } catch { }
                            diagnostic.Add(new { on = src.GetType().Name, field = f.Name, type = f.FieldType.Name, count = c });
                        }
                        else if (v != null)
                        {
                            var tn = v.GetType().Name;
                            if (tn.StartsWith("Net") || tn.Contains("Buff") || tn.Contains("List") || tn.Contains("Dict"))
                                diagnostic.Add(new { on = src.GetType().Name, field = f.Name, type = f.FieldType.Name, value = v.ToString()?.Substring(0, Math.Min(40, (v.ToString()?.Length ?? 0))) });
                        }
                    }
                }

                if (container != null)
                {
                    // 取 Buff 对象列表（dict 取 Values，list 直接枚举）
                    var buffs = new List<object>();
                    if (container is System.Collections.IDictionary dict)
                        buffs.AddRange(dict.Values.OfType<object>());
                    else if (container is System.Collections.IEnumerable en)
                        buffs.AddRange(en.OfType<object>());

                    foreach (var b in buffs)
                    {
                        var bt = b.GetType();
                        object? Get(string n)
                        {
                            try
                            {
                                var p = bt.GetProperty(n, flags);
                                if (p != null) return p.GetValue(b);
                                var f = bt.GetField(n, flags);
                                if (f != null) return f.GetValue(b);
                            }
                            catch { }
                            return null;
                        }
                        var src = Get("source")?.ToString() ?? Get("Source")?.ToString() ?? "";
                        var dn = Get("displayName")?.ToString() ?? Get("DisplayName")?.ToString() ?? "";
                        var bid = Get("id")?.ToString() ?? Get("BuffId")?.ToString() ?? Get("which")?.ToString() ?? "";
                        var ms = Get("millisecondsDuration") ?? Get("MillisecondsDuration") ?? Get("msDuration");
                        int msInt = ms is int i ? i : (ms is long l ? (int)l : 0);
                        list.Add(new
                        {
                            id = bid,
                            source = src,
                            displayName = dn,
                            msRemaining = msInt,
                            seconds = msInt / 1000
                        });
                    }
                }
                tcs.SetResult(new { ok = true, count = list.Count, buffs = list, diagnostic });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// GET /mastery — 精通状态（SDV 1.6）。
    /// 反射读 Farmer 上所有含 Mastery 的字段/属性（MasteryExp/MasteryLevelsSpent 等），
    /// 兼容各 1.6 小版本命名。经验值检测 + 已领取点数检测都靠它。
    /// </summary>
    private object HandleMastery()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var farmer = Game1.player;
                var t = typeof(Farmer);
                var flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;
                var fields = new List<object>();
                foreach (var f in t.GetFields(flags).Where(f => f.Name.Contains("Mastery", StringComparison.OrdinalIgnoreCase)))
                {
                    object? v = null;
                    try { v = f.GetValue(farmer); } catch { }
                    v = UnwrapNetValue(v);
                    fields.Add(new { name = f.Name, value = v?.ToString() ?? "(null)" });
                }
                var props = new List<object>();
                foreach (var pr in t.GetProperties(flags).Where(p => p.Name.Contains("Mastery", StringComparison.OrdinalIgnoreCase)))
                {
                    object? v = null;
                    try { v = pr.GetValue(farmer); } catch { }
                    v = UnwrapNetValue(v);
                    props.Add(new { name = pr.Name, value = v?.ToString() ?? "(null)" });
                }
                tcs.SetResult(new { ok = true, fields, props });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// GET /special_items
    /// 钱包特殊物品列表（Game1.player.specialItems / NetStringList）。
    /// 1.6 精通（mastery_farming/mining/combat/foraging/fishing）、小镇钥匙(TownKey)、
    /// 头骨/生锈钥匙等都在这里——与 mailReceived flag 是两个独立钱包源。
    /// has 子对象预解析成 bool，方便 Python 直接查"是否已领取某项"。
    /// ⚠️ specialItems 是 Net 类型，必须主线程读（同 /surroundings 主线程化教训）。
    /// </summary>
    private object HandleSpecialItems()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var farmer = Game1.player;
                var items = (farmer.specialItems?.ToList()) ?? new List<string>();
                var has = new Dictionary<string, bool>
                {
                    ["mastery_farming"] = items.Contains("mastery_farming"),
                    ["mastery_mining"] = items.Contains("mastery_mining"),
                    ["mastery_combat"] = items.Contains("mastery_combat"),
                    ["mastery_foraging"] = items.Contains("mastery_foraging"),
                    ["mastery_fishing"] = items.Contains("mastery_fishing"),
                    ["TownKey"] = items.Contains("TownKey"),
                };
                tcs.SetResult(new { ok = true, specialItems = items, has });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// GET /achievement_probe — 成就探测（只读，2026-08-23 恒）。
    /// 反射 dump Game1.player.stats 上所有含 Achiev 的字段（来自存档 XML：&lt;achievements&gt;&lt;int&gt;ID&lt;/int&gt;...）。
    /// 确认 SDV 1.6 成就的确切存储/类型后再决定正式接入。
    /// </summary>
    private object HandleAchievementProbe()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var farmer = Game1.player;
                // 成就点在 Game1.player.achievements（NetIntHashSet，反编译 Farmer:243 确认）
                var achv = farmer?.achievements;
                var list = new List<object>();
                if (achv != null)
                {
                    foreach (var a in achv)
                    {
                        string name = "";
                        try
                        {
                            // Game1.achievements 是 Dictionary<int,string> "名^描述"（反编译 Game1:820）
                            if (Game1.achievements != null && Game1.achievements.TryGetValue(a, out var raw))
                                name = raw.Split('^')[0];
                        }
                        catch { }
                        list.Add(new { id = a, name });
                    }
                }
                tcs.SetResult(new
                {
                    ok = true,
                    field = "player.achievements(NetIntHashSet)",
                    count = list.Count,
                    achievements = list,
                });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    private static object? UnwrapNetValue(object? v)
    {
        if (v == null) return null;
        var tn = v.GetType().Name;
        if (tn.StartsWith("NetInt") || tn.StartsWith("NetString") || tn.StartsWith("NetBool"))
        {
            try { return v.GetType().GetProperty("Value")?.GetValue(v); } catch { }
        }
        if (v is System.Collections.IEnumerable en and not string)
        {
            var items = new List<string>();
            try
            {
                foreach (var it in en) items.Add(it?.ToString() ?? "?");
            }
            catch { }
            if (items.Count > 0) return "[" + string.Join(", ", items) + "]";
        }
        return v;
    }

    /// <summary>
    /// POST /mastery_claim  { type: "Farming" }
    /// 领取精通。1.6 各版本的领取方法名不一致（无统一 claimMastery），
    /// 此端点先用反射列出 Farmer 上所有含 Mastery 的方法供确认，
    /// 并顺手报告当前精通经验。真正的领取 API 确认后一步到位。
    /// </summary>
    private object HandleMasteryClaim(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var typeName = GetParamOr(p, "type", "");

        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var farmer = Game1.player;
                var flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;
                var methods = typeof(Farmer).GetMethods(flags)
                    .Where(m => m.Name.Contains("Mastery", StringComparison.OrdinalIgnoreCase))
                    .Select(m => m.Name).Distinct().OrderBy(n => n).ToList();
                object? expObj = null;
                try
                {
                    var f = typeof(Farmer).GetField("MasteryExp", flags);
                    expObj = f != null ? UnwrapNetValue(f.GetValue(farmer)) : null;
                }
                catch { }
                tcs.SetResult(new
                {
                    ok = true,
                    requestedType = typeName,
                    masteryExp = expObj?.ToString() ?? "?",
                    candidateClaimMethods = methods
                });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// GET /carpenter — 木匠商店建筑清单（只读）。
    /// 从 Game1.buildingData 列所有建筑：价格 + 材料 + 尺寸 + 是否买得起。
    /// 1.6 建筑系统是数据驱动的（无 BluePrint 类），建造写操作暂不做（防误扣资源）。
    /// </summary>
    private object HandleCarpenter()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var farmer = Game1.player;
                var buildings = new List<object>();
                int affordable = 0;
                foreach (var kv in Game1.buildingData)
                {
                    var d = kv.Value;
                    if (d == null) continue;
                    var mats = new List<object>();
                    bool hasMats = true;
                    if (d.BuildMaterials != null)
                    {
                        foreach (var m in d.BuildMaterials)
                        {
                            mats.Add(new { id = m.ItemId, amount = m.Amount });
                            var it = StardewValley.ItemRegistry.GetDataOrErrorItem(m.ItemId);
                            hasMats &= farmer.getItemCount(it.QualifiedItemId) >= m.Amount;
                        }
                    }
                    var cost = d.BuildCost;
                    bool affordableThis = farmer.Money >= cost && hasMats;
                    if (affordableThis) affordable++;
                    buildings.Add(new
                    {
                        name = kv.Key,
                        cost,
                        size = d.Size,
                        materials = mats,
                        affordable = affordableThis
                    });
                }
                tcs.SetResult(new { ok = true, count = buildings.Count, affordable, buildings });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// GET /ladder
    /// 检测当前矿洞的梯子和竖井位置。
    /// 直接读 MineShaft.netTileBeneathLadder 字段（SDV 1.6 实测可用）。
    /// </summary>
    private object HandleLadder()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var loc = Game1.player?.currentLocation;
        if (loc == null)
            return new { ok = false, error = "No location" };

        var locName = loc.Name;
        if (locName == null || !locName.StartsWith("UndergroundMine"))
            return new { ok = false, error = "Not in a mine", location = locName };

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                int level = -1;
                try { level = Convert.ToInt32(locName.Replace("UndergroundMine", "")); }
                catch { }

                Vector2? downLadder = null;
                Vector2? shaftPos = null;
                bool spawned = false;
                Vector2 entrancePos = new(-1, -1);

                var type = loc.GetType();
                var flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;

                // ── 1. 读 ladderHasSpawned ──
                try
                {
                    var f = type.GetField("ladderHasSpawned", flags);
                    if (f != null)
                        spawned = Convert.ToBoolean(f.GetValue(loc));
                }
                catch { }

                // ── 2. 读 netTileBeneathLadder（入口梯子位置） ──
                try
                {
                    var f = type.GetField("netTileBeneathLadder", flags);
                    if (f != null)
                    {
                        var val = f.GetValue(loc);
                        if (val != null)
                        {
                            var vt = val.GetType();
                            var xp = vt.GetProperty("X") ?? vt.GetProperty("x");
                            var yp = vt.GetProperty("Y") ?? vt.GetProperty("y");
                            if (xp != null && yp != null)
                                entrancePos = new Vector2(
                                    Convert.ToSingle(xp.GetValue(val)),
                                    Convert.ToSingle(yp.GetValue(val)));
                        }
                    }
                }
                catch { }

                // ── 3. 扫 Buildings 图层找所有 tile index 173（梯子） ──
                // 入口梯子 + 下层梯子都是 173，要排除入口找到下层
                try
                {
                    var buildings = loc.Map?.GetLayer("Buildings");
                    if (buildings != null)
                    {
                        for (int x = 0; x < buildings.LayerWidth; x++)
                        {
                            for (int y = 0; y < buildings.LayerHeight; y++)
                            {
                                var tile = buildings.Tiles[x, y];
                                if (tile == null) continue;
                                var pos = new Vector2(x, y);

                                if (tile.TileIndex == 173)
                                {
                                    // 不是入口位置的 173 = 下楼梯子。
                                    // 去掉 spawned 限制：打飞蛇/敲石头爆出的梯子，ladderHasSpawned 字段可能没同步，
                                    // 但 Buildings 图层已经有 173——只要非入口就该检测到。
                                    if (pos != entrancePos && downLadder == null)
                                        downLadder = pos;
                                }
                                else if (tile.TileIndex == 174)
                                {
                                    if (shaftPos == null)
                                        shaftPos = pos;
                                }
                            }
                        }
                    }
                }
                catch { }

                // ── 如果 spawned 但没找到非入口 173，可能是 SDV 1.6 不同 tile index ──
                // 回退：直接返回所有非入口位置的 173/174
                if (downLadder == null && spawned)
                {
                    try
                    {
                        var buildings = loc.Map?.GetLayer("Buildings");
                        if (buildings != null)
                        {
                            for (int x = 0; x < buildings.LayerWidth; x++)
                            {
                                for (int y = 0; y < buildings.LayerHeight; y++)
                                {
                                    var tile = buildings.Tiles[x, y];
                                    if (tile == null) continue;
                                    var pos = new Vector2(x, y);
                                    if (pos == entrancePos) continue;
                                    if ((tile.TileIndex == 173 || tile.TileIndex == 1375 || tile.TileIndex == 1376) && downLadder == null)
                                        downLadder = pos;
                                    else if ((tile.TileIndex == 174 || tile.TileIndex == 1381 || tile.TileIndex == 1382) && shaftPos == null)
                                        shaftPos = pos;
                                }
                            }
                        }
                    }
                    catch { }
                }

                tcs.SetResult(new
                {
                    ok = true,
                    location = locName,
                    level,
                    ladderSpawned = spawned,
                    entrance = new { x = (int)entrancePos.X, y = (int)entrancePos.Y },
                    ladder = downLadder != null ? new { x = (int)downLadder.Value.X, y = (int)downLadder.Value.Y } : null,
                    shaft = shaftPos != null ? new { x = (int)shaftPos.Value.X, y = (int)shaftPos.Value.Y } : null,
                });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// GET /mine_debug
    /// 调试用：dump MineShaft 的所有字段和当前值，找梯子字段。
    /// </summary>
    private object HandleMineDebug()
    {
        if (!Context.IsWorldReady)
            return new { ok = false, error = "World not ready" };

        var loc = Game1.player?.currentLocation;
        if (loc == null)
            return new { ok = false, error = "No location" };

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var type = loc.GetType();
                var fields = type.GetFields(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                var fieldInfos = new List<object>();

                foreach (var f in fields)
                {
                    try
                    {
                        var val = f.GetValue(loc);
                        string valStr = val?.ToString() ?? "null";
                        if (val is Vector2 v)
                            valStr = $"Vector2({v.X},{v.Y})";
                        else if (val is Point pt)
                            valStr = $"Point({pt.X},{pt.Y})";
                        else if (val is System.Collections.IDictionary dict)
                            valStr = $"Dict[{dict.Count}]";

                        // Truncate long values
                        if (valStr.Length > 80)
                            valStr = valStr[..80] + "...";

                        fieldInfos.Add(new
                        {
                            name = f.Name,
                            type = f.FieldType.Name,
                            value = valStr
                        });
                    }
                    catch { }
                }

                // Sort: ladder/shaft related fields first
                fieldInfos.Sort((a, b) =>
                {
                    var an = (string)((dynamic)a).name;
                    var bn = (string)((dynamic)b).name;
                    bool aRelevant = an.IndexOf("ladder", StringComparison.OrdinalIgnoreCase) >= 0
                                    || an.IndexOf("shaft", StringComparison.OrdinalIgnoreCase) >= 0
                                    || an.IndexOf("mine", StringComparison.OrdinalIgnoreCase) >= 0
                                    || an.IndexOf("hole", StringComparison.OrdinalIgnoreCase) >= 0;
                    bool bRelevant = bn.IndexOf("ladder", StringComparison.OrdinalIgnoreCase) >= 0
                                    || bn.IndexOf("shaft", StringComparison.OrdinalIgnoreCase) >= 0
                                    || bn.IndexOf("mine", StringComparison.OrdinalIgnoreCase) >= 0
                                    || bn.IndexOf("hole", StringComparison.OrdinalIgnoreCase) >= 0;
                    if (aRelevant != bRelevant) return aRelevant ? -1 : 1;
                    return string.Compare(an, bn);
                });

                tcs.SetResult(new
                {
                    ok = true,
                    location = loc.Name,
                    typeName = type.FullName,
                    fieldCount = fields.Length,
                    fields = fieldInfos,
                });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// GET /mine/elevator
    /// 读鹈鹕镇矿井电梯当前可达楼层（动态起始层用，2026-08-22）。
    /// 接齐先生"深处的危险"会重置电梯 → 楼层为空 → reset=true → 起始=1。
    /// 构造一个 throwaway MineElevatorMenu（SDV 自己的电梯选单，反映真实解锁状态），
    /// 反射其楼层按钮组件 elevators 等 List&lt;ClickableComponent&gt;，把组件 name/label 解析为 int 得楼层列表；
    /// 无论成败都还原 Game1.activeClickableMenu，避免副作用。
    /// </summary>
    private object HandleMineElevator()
    {
        if (!Context.IsWorldReady)
            return new { ok = false, error = "World not ready" };

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            var prevMenu = Game1.activeClickableMenu;
            try
            {
                var floors = new List<int>();
                // ① 若当前已打开电梯选单，直接反射真实呈现的楼层；否则构造一个 throwaway 选单读游戏真相
                IClickableMenu? em = Game1.activeClickableMenu is StardewValley.Menus.MineElevatorMenu m ? m
                                   : new StardewValley.Menus.MineElevatorMenu();
                var flags = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic
                            | System.Reflection.BindingFlags.Instance;
                for (var t = em.GetType(); t != null; t = t.BaseType)
                {
                    foreach (var f in t.GetFields(flags))
                    {
                        if (!typeof(List<StardewValley.Menus.ClickableComponent>).IsAssignableFrom(f.FieldType))
                            continue;
                        if (f.GetValue(em) is System.Collections.IEnumerable comps)
                        {
                            foreach (var c in comps)
                            {
                                if (c is StardewValley.Menus.ClickableComponent cc && cc != null)
                                {
                                    int n = -1;
                                    if (!string.IsNullOrEmpty(cc.name) && int.TryParse(cc.name, out n))
                                    { }
                                    else if (!string.IsNullOrEmpty(cc.label) && int.TryParse(cc.label, out n))
                                    { }
                                    if (n >= 1 && n <= 120)
                                        floors.Add(n);
                                }
                            }
                        }
                    }
                }
                floors = floors.Distinct().OrderBy(x => x).ToList();
                var reset = floors.Count == 0;
                var maxFloor = reset ? 1 : floors.Max();
                // 诊断：当前所在地下层（MineShaft.netMineLevel 私有，用反射读），看电梯可达层 vs 实际层
                var curLevel = 0;
                try
                {
                    var curLoc = Game1.player?.currentLocation;
                    if (curLoc != null)
                    {
                        var fNet = curLoc.GetType().GetField("netMineLevel", flags);
                        if (fNet?.GetValue(curLoc) is Netcode.NetInt curShaftLevel)
                            curLevel = curShaftLevel.Value;
                    }
                }
                catch { }
                tcs.SetResult(new { ok = true, floors = floors, maxFloor = maxFloor, reset = reset, currentLevel = curLevel });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message, floors = new List<int>(), maxFloor = 1, reset = true, currentLevel = 0 });
            }
            finally
            {
                Game1.activeClickableMenu = prevMenu;   // 还原，避免副作用
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    private object HandleFestival()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var evt = Game1.CurrentEvent;
                if (evt == null)
                {
                    tcs.SetResult(new { ok = false, error = "No active event" });
                    return;
                }

                var flags = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                var actors = new List<object>();

                // Get event actors
                var actorsField = evt.GetType().GetField("actors", flags);
                if (actorsField?.GetValue(evt) is IEnumerable<NPC> npcList)
                {
                    foreach (var npc in npcList)
                    {
                        actors.Add(new
                        {
                            name = npc.Name,
                            displayName = npc.displayName,
                            x = npc.TilePoint.X,
                            y = npc.TilePoint.Y
                        });
                    }
                }

                // Check festival name
                string festivalName = "";
                var nameField = evt.GetType().GetField("FestivalName", flags) ?? evt.GetType().GetField("festivalName", flags);
                if (nameField != null)
                    festivalName = nameField.GetValue(evt) as string ?? "";
                var nameProp = evt.GetType().GetProperty("FestivalName", flags);
                if (string.IsNullOrEmpty(festivalName) && nameProp != null)
                    festivalName = nameProp.GetValue(evt) as string ?? "";

                // Check isFestival
                bool isFestival = false;
                var isFestMethod = typeof(Game1).GetMethod("isFestival", flags, null, Type.EmptyTypes, null);
                if (isFestMethod != null)
                    isFestival = (bool?)isFestMethod.Invoke(null, null) ?? false;

                // 🥚 寻宝倒计时（festivalTimer 字段：>0 = 限时小游戏进行中，蛋蛋节找蛋/冰钓等用，2026-08-17）
                int festivalTimer = -1;
                var timerField = evt.GetType().GetField("festivalTimer", flags);
                if (timerField != null && timerField.GetValue(evt) is int tf) festivalTimer = tf;
                int festivalScore = Game1.player.festivalScore;

                tcs.SetResult(new
                {
                    ok = true,
                    isFestival,
                    festivalName,
                    location = Game1.currentLocation?.Name,
                    actorCount = actors.Count,
                    festivalTimer,     // 🥚 限时小游戏倒计时(ms)，>0=进行中（2026-08-17）
                    festivalScore,     // 🥚 当前玩家 festivalScore（捡蛋数）
                    actors
                });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>📜 节日事件数据转储（2026-08-17 恒：反编译蛋蛋位置用）。
    /// 读 Data/Festivals/{season}{day} 的 Dictionary&lt;string,string&gt;（事件key→脚本），
    /// 脚本里含找蛋/摆摊等坐标。GET /festival_data?season=spring&amp;day=13（默认今天）。
    /// ⚠️ 内容直接读游戏已加载的 xnb 数据，省得外部解 LZX。</summary>
    private object HandleFestivalData(HttpListenerContext ctx)
    {
        var q = ctx.Request.QueryString;
        string season = q["season"] ?? Game1.currentSeason;
        string day = q["day"] ?? Game1.dayOfMonth.ToString();
        string asset = $"Data/Festivals/{season}{day}";

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var data = Game1.content.Load<Dictionary<string, string>>(asset);
                var entries = new List<object>();
                foreach (var kv in data)
                {
                    entries.Add(new { key = kv.Key, script = kv.Value });
                }
                tcs.SetResult(new { ok = true, asset, count = entries.Count, entries });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, asset, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>🥚 扫地图 Paths 图层 TileSheet 以 "fest" 开头的图块 = 蛋蛋坐标（2026-08-17 恒）。
    /// Event.cs 的 eggHunt 处理器就是这样找蛋（festivalProps.Add）。
    /// GET /egg_tiles?map=Town-EggFestival2（map 省略=当前地图）。奇偶年地图不同：Town-EggFestival / Town-EggFestival2。</summary>
    private object HandleEggTiles(HttpListenerContext ctx)
    {
        var q = ctx.Request.QueryString;
        string mapName = q["map"] ?? "";   // 空 = 当前地图

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                xTile.Map map = string.IsNullOrEmpty(mapName)
                    ? Game1.currentLocation.map
                    : Game1.content.Load<xTile.Map>("Maps/" + mapName);
                var layer = map.GetLayer("Paths");
                if (layer == null)
                {
                    tcs.SetResult(new { ok = true, map = mapName, count = 0, tiles = new List<object>() });
                    return;
                }
                var tiles = new List<object>();
                for (int k = 0; k < layer.LayerWidth; k++)
                {
                    for (int l = 0; l < layer.LayerHeight; l++)
                    {
                        var tile = layer.Tiles[k, l];
                        if (tile != null && tile.TileSheet.Id.StartsWith("fest"))
                            tiles.Add(new { x = k, y = l, tileIndex = tile.TileIndex, sheet = tile.TileSheet.Id });
                    }
                }
                tcs.SetResult(new { ok = true, map = string.IsNullOrEmpty(mapName) ? Game1.currentLocation?.Name : mapName, count = tiles.Count, tiles });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    private object HandleFestivalInteract(HttpListenerContext ctx)
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var body = ReadJson(ctx);
        string targetName = body.ContainsKey("name") ? body["name"].ToString() : "";

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var evt = Game1.CurrentEvent;
                if (evt == null)
                {
                    tcs.SetResult(new { ok = false, error = "No active event" });
                    return;
                }

                var flags = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;
                var actorsField = evt.GetType().GetField("actors", flags);
                if (actorsField?.GetValue(evt) is not IEnumerable<NPC> npcList)
                {
                    tcs.SetResult(new { ok = false, error = "No actors found" });
                    return;
                }

                NPC? target = null;
                foreach (var npc in npcList)
                {
                    // ⚠️ 2026-08-16 恒：中文名适配——匹配 Name 或 displayName（罗宾→Robin）
                    bool nameHit = string.IsNullOrEmpty(targetName)
                        || npc.Name.Equals(targetName, StringComparison.OrdinalIgnoreCase)
                        || (npc.displayName ?? "").Equals(targetName, StringComparison.OrdinalIgnoreCase)
                        || (npc.displayName ?? "").Contains(targetName, StringComparison.OrdinalIgnoreCase);
                    if (nameHit)
                    {
                        target = npc;
                        break;
                    }
                }

                if (target == null)
                {
                    tcs.SetResult(new { ok = false, error = $"Actor '{targetName}' not found" });
                    return;
                }

                // Move player next to NPC and face them
                var farmer = Game1.player;
                farmer.Position = new Vector2(target.TilePoint.X, target.TilePoint.Y + 1) * Game1.tileSize;
                farmer.faceDirection(0); // face up toward NPC

                // Try to trigger NPC action via checkAction
                bool triggered = Game1.currentLocation.checkAction(
                    new xTile.Dimensions.Location(target.TilePoint.X, target.TilePoint.Y),
                    Game1.viewport, farmer);

                if (!triggered)
                {
                    // Fallback: try direct NPC click
                    target.checkAction(farmer, Game1.currentLocation);
                    triggered = true;
                }

                tcs.SetResult(new
                {
                    ok = true,
                    target = target.Name,
                    targetTile = new { x = target.TilePoint.X, y = target.TilePoint.Y },
                    playerTile = new { x = farmer.TilePoint.X, y = farmer.TilePoint.Y },
                    triggered
                });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    private object HandleFestivalAnswer(HttpListenerContext ctx)
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var body = ReadJson(ctx);
        int answer = GetParamOr(body, "answer", 0);   // 🆕 2026-08-18 修 JsonElement cast 崩（原 Convert.ToInt32）
        string key = body.ContainsKey("key") ? body["key"].ToString() : "";

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var evt = Game1.CurrentEvent;
                if (evt == null)
                {
                    tcs.SetResult(new { ok = false, error = "No active event" });
                    return;
                }

                var flags = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic;

                // Try answerDialogueQuestion
                var answerMethod = evt.GetType().GetMethod("answerDialogueQuestion", flags);
                if (answerMethod != null)
                {
                    var npc = Game1.currentLocation.isCharacterAtTile(Game1.player.GetGrabTile());
                    // 🆕 2026-08-18：answerDialogueQuestion 期望 responseKey（不是 index），从当前 DialogueBox 取
                    string answerKey = answer.ToString();
                    if (Game1.activeClickableMenu is DialogueBox db2)
                    {
                        var rf2 = typeof(DialogueBox).GetField("responses", flags);
                        var arr2 = rf2?.GetValue(db2) as IEnumerable<Response>;
                        var list2 = arr2?.ToList();
                        if (list2 != null && answer >= 0 && answer < list2.Count) answerKey = list2[answer].responseKey;
                    }
                    answerMethod.Invoke(evt, new object?[] { npc, answerKey });
                    tcs.SetResult(new { ok = true, method = "answerDialogueQuestion", answer, key = answerKey });
                    return;
                }

                // Fallback: try answerDialogue on the event
                var methods = evt.GetType().GetMethods(flags);
                foreach (var m in methods)
                {
                    if (m.Name.Contains("answer", StringComparison.OrdinalIgnoreCase) ||
                        m.Name.Contains("Answer", StringComparison.OrdinalIgnoreCase))
                    {
                        var parms = m.GetParameters();
                        if (parms.Length >= 1)
                        {
                            try
                            {
                                if (parms[0].ParameterType == typeof(int))
                                    m.Invoke(evt, new object[] { answer });
                                else if (parms[0].ParameterType == typeof(string))
                                    m.Invoke(evt, new object[] { answer.ToString() });
                                tcs.SetResult(new { ok = true, method = m.Name, answer });
                                return;
                            }
                            catch { continue; }
                        }
                    }
                }

                // Fallback: use Game1.currentLocation.answerDialogueAction
                var locMethod = Game1.currentLocation.GetType().GetMethod("answerDialogueAction", flags);
                if (locMethod != null)
                {
                    string actionKey = string.IsNullOrEmpty(key) ? $"festival_{answer}" : key;
                    locMethod.Invoke(Game1.currentLocation, new object[] { actionKey, Array.Empty<string>() });
                    tcs.SetResult(new { ok = true, method = "location.answerDialogueAction", key = actionKey });
                    return;
                }

                tcs.SetResult(new { ok = false, error = "No answer method found" });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// POST /dance_invite  ?target=名字
    /// 💃 花舞节邀请玩家跳舞（2026-08-21：direct=true 作弊已退役，正常端口走通）。
    /// 走 team.SendProposal(ProposalType.Dance)：本玩家发提案，对方弹 "AskedToDance" Yes/No 正常接受，
    /// 接受后双方 dancePartner 各自动设好（PendingProposalDialog/回调）。
    /// ⚠️ 本端点只收玩家（邀 NPC 走自然 interact 流程，见 MCP 引导）；dancePartner 须在舞会开始前设好；farmhand 需在花舞节事件中。
    /// </summary>
    private object HandleDanceInvite(HttpListenerContext ctx)
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var p = ReadJson(ctx);
        var target = GetParamOr(p, "target", "");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var inFlowerDance = Game1.CurrentEvent != null && Game1.CurrentEvent.isSpecificFestival("spring24");

                // 🎯 目标解析：先玩家（按名字，默认=另一个在线玩家），再节日 NPC（只提示走自然流程）
                var others = Game1.getOnlineFarmers()
                    .Where(f => f.UniqueMultiplayerID != Game1.player.UniqueMultiplayerID)
                    .ToList();
                Farmer? targetFarmer = null;
                if (!string.IsNullOrEmpty(target))
                    targetFarmer = others.FirstOrDefault(f => f.Name.Equals(target, StringComparison.OrdinalIgnoreCase));

                // 💃 玩家没匹配上 → 试节日 NPC：只提示走自然流程（本端点只收玩家，direct=true 已退役）
                if (targetFarmer == null && !string.IsNullOrEmpty(target) && Game1.CurrentEvent != null)
                {
                    var evt = Game1.CurrentEvent;
                    NPC? targetNpc = evt.getActorByName(target);
                    if (targetNpc == null)
                    {
                        var actorsField = evt.GetType().GetField("actors",
                            System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic |
                            System.Reflection.BindingFlags.Instance);
                        if (actorsField?.GetValue(evt) is IEnumerable<NPC> npcList)
                        {
                            targetNpc = npcList.FirstOrDefault(a =>
                                (a.displayName ?? "").Equals(target, StringComparison.OrdinalIgnoreCase) ||
                                (a.displayName ?? "").Contains(target, StringComparison.OrdinalIgnoreCase) ||
                                a.Name.Contains(target, StringComparison.OrdinalIgnoreCase));
                        }
                    }
                    if (targetNpc != null)
                    {
                        tcs.SetResult(new { ok = false, error = $"邀 NPC {targetNpc.Name} 请走自然流程：站 NPC 紧邻格裸 /interact → 弹「什么事？」→ 选「邀请XX作舞伴」（需4心+）。本端点只收玩家" });
                        return;
                    }
                }

                // ⚠️ 指定了名字但没找到 → 明确报错，绝不静默回退到默认玩家（2026-08-20 修：艾米丽没匹配上回退配错成恒）
                if (targetFarmer == null && !string.IsNullOrEmpty(target))
                {
                    tcs.SetResult(new { ok = false, error = $"找不到目标 '{target}'（无此在线玩家，也无此节日 NPC——英文环境请用英文名如 Emily；空 target 才默认配另一个玩家）" });
                    return;
                }
                targetFarmer ??= others.FirstOrDefault();
                if (targetFarmer == null)
                {
                    tcs.SetResult(new { ok = false, error = "目标为空且无其他在线玩家（需要 target 指定目标）" });
                    return;
                }

                // 🥂 走提案系统（对方弹 "AskedToDance" Yes/No 接受；本方弹等待框 PendingProposalDialog，等对方应答自动关）
                Game1.player.team.SendProposal(targetFarmer, ProposalType.Dance);
                string warn2 = inFlowerDance ? "" : "（⚠️ 当前不在花舞节事件中，对方可能拒绝——确认双方已在花舞节地图）";
                tcs.SetResult(new
                {
                    ok = true,
                    action = "proposal_sent",
                    target = targetFarmer.Name,
                    note = $"已发送跳舞邀请给 {targetFarmer.Name}，等对方接受{warn2}"
                });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// GET /unlocks
    /// 读游戏解锁状态（邮件 flag / 工具等级 / 技能）→ map_go 据此拦截未解锁地点。
    /// 2026-08-14：mail flag 名 best-effort，需游戏重启后实测核对。
    /// </summary>
    private object HandleUnlocks()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var master = Game1.MasterPlayer;
                var mail = master?.mailReceived;

                bool Has(string flag) => mail != null && mail.Contains(flag);

                // ⚠️ 会员卡是 per-player 物品（2026-08-23 恒：7842恒无卡/7843 AI 有卡）——必须读"当前角色"而非 MasterPlayer。
                // /unlocks 恒从 AI 进程(7843)打 → Game1.player = 实际负责走位开赌场的 AI；恒(7842)=另一角色，无卡。
                // 正确判定=Farmer.hasClubCard 属性（反编译 farmer.cs:1332，读自己 mailReceived 的 "HasClubCard" flag，非物理物品、非 "clubCard"）。
                // 其余全局解锁（矿/巴士/下水道等）仍走 Has（读 MasterPlayer=房主权威端），勿混。

                // 钢斧（秘密森林）：Axe UpgradeLevel >= 2
                int axeLevel = 0;
                try
                {
                    if (master?.getToolFromName("Axe") is Tool axe)
                        axeLevel = axe.UpgradeLevel;
                }
                catch { }

                // 精通山洞：全技能 10 级
                bool mastery = false;
                try
                {
                    var p = master ?? Game1.player;
                    if (p != null && p.farmingLevel.Value >= 10 && p.miningLevel.Value >= 10
                        && p.foragingLevel.Value >= 10 && p.fishingLevel.Value >= 10 && p.combatLevel.Value >= 10)
                        mastery = true;
                }
                catch { }

                // 山顶：100% 完美达成（best-effort 反射探 FarmerTeam 里 Perfection 相关属性）
                bool summit = false;
                try
                {
                    var nwsValue = Game1.netWorldState?.Value;
                    if (nwsValue != null)
                    {
                        var flags = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Instance;
                        foreach (var prop in nwsValue.GetType().GetProperties(flags)
                                     .Where(p => p.Name.ToLower().Contains("perfection")))
                        {
                            try
                            {
                                var v = prop.GetValue(nwsValue);
                                if (v != null && Convert.ToDouble(v) >= 1.0) { summit = true; break; }
                            }
                            catch { }
                        }
                    }
                }
                catch { }

                // ⚠️ 2026-08-14 校准（用 /unlock_debug 从全解锁旧档实测的真实 flag）：
                //   HasRustyKey / HasSkullKey / HasTownKey / ccMovieTheater / Pam_cc_Bus / ccDoorUnlock
                // 矿洞/铁路按日期（用户拍板：不用 mail flag，直接天数记）
                bool railroadOpen = Game1.year > 1
                    || (Game1.currentSeason == "summer" && Game1.dayOfMonth >= 3)
                    || Game1.currentSeason == "fall" || Game1.currentSeason == "winter";
                bool mineOpen = Game1.year > 1 || Game1.currentSeason != "spring"
                    || (Game1.currentSeason == "spring" && Game1.dayOfMonth >= 5);
                // 鹦鹉特快（姜岛快捷/图腾，2026-08-15 恒：需喂金核桃解锁）
                //   FarmerTeam.parrotUpgradesDone（NetList<int,NetInt>，已完成的升级 index 列表）。
                //   ⚠️ 用 Count+索引器 反射读（NetList 只实现泛型 IEnumerable<T>，is IEnumerable 判空会静默失败）。
                //   parrotUpgradesRaw 输出原始列表，parrotDiag 说明读取状态（供校准）。
                // 鹦鹉特快/岛升级（2026-08-15 恒：需喂金核桃解锁）。
                //   ⚠️ SDV1.6 字段名实测不在 master.team.parrotUpgradesDone（diag=no-field）→
                //   改为自适应：扫 team/netWorldState 里名字含 parrot/upgrade/walnut 的成员，
                //   读第一个 Count>0 的集合（yr3解锁档非空=✅，yr2未解锁档空=🔒）。
                // 鹦鹉平台/岛升级（2026-08-15 实测字段）：SDV1.6 存于
                //   nws.parrotPlatformsUnlocked（NetList<int>，已解锁平台索引）+ 各岛 GameLocation.parrotPlatforms。
                //   ⚠️ 不是 FarmerTeam.parrotUpgradesDone（之前猜错）。
                bool parrotExpress = false;
                var parrotUpgradesRaw = new List<int>();
                string parrotDiag = "not-read";
                var parrotMembers = new List<string>();
                try
                {
                    var bf = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic;
                    var nws = Game1.netWorldState?.Value;

                    // 1. 首选 nws.parrotPlatformsUnlocked（规范：已解锁平台列表）
                    if (nws != null)
                    {
                        var f = nws.GetType().GetField("parrotPlatformsUnlocked", bf)
                                ?? nws.GetType().GetField("ParrotPlatformsUnlocked", bf);
                        if (f != null)
                        {
                            var listObj = f.GetValue(nws);
                            if (listObj != null)
                            {
                                int count = 0;
                                try { count = Convert.ToInt32(listObj.GetType().GetProperty("Count")?.GetValue(listObj)); } catch { }
                                parrotMembers.Add($"nws.parrotPlatformsUnlocked={count}");
                                for (int i = 0; i < count; i++)
                                {
                                    object? item = null;
                                    try { item = listObj.GetType().GetProperty("Item")?.GetValue(listObj, new object[] { i }); } catch { }
                                    int v = 0;
                                    if (item is int iint) v = iint;
                                    else
                                    {
                                        try { v = Convert.ToInt32(item); }
                                        catch { try { v = Convert.ToInt32(item?.GetType().GetProperty("Value")?.GetValue(item)); } catch { } }
                                    }
                                    parrotUpgradesRaw.Add(v);
                                }
                                if (parrotUpgradesRaw.Count > 0)
                                    parrotDiag = $"from:nws.parrotPlatformsUnlocked({count})";
                            }
                        }
                    }

                    // 2. 扫各岛 GameLocation 的 parrotPlatforms（补数据，含 IslandSouth 度假村）
                    foreach (var locName in new[] { "IslandWest", "IslandSouth", "IslandNorth", "IslandEast" })
                    {
                        try
                        {
                            var loc = Game1.getLocationFromName(locName);
                            if (loc == null) continue;
                            var f = loc.GetType().GetField("parrotPlatforms", bf)
                                    ?? loc.GetType().GetField("ParrotPlatforms", bf);
                            if (f == null) continue;
                            var listObj = f.GetValue(loc);
                            if (listObj == null) continue;
                            int count = 0;
                            try { count = Convert.ToInt32(listObj.GetType().GetProperty("Count")?.GetValue(listObj)); } catch { }
                            var vals = new List<int>();
                            for (int i = 0; i < count; i++)
                            {
                                object? item = null;
                                try { item = listObj.GetType().GetProperty("Item")?.GetValue(listObj, new object[] { i }); } catch { }
                                int v = 0;
                                if (item is int iint) v = iint;
                                else
                                {
                                    try { v = Convert.ToInt32(item); }
                                    catch { try { v = Convert.ToInt32(item?.GetType().GetProperty("Value")?.GetValue(item)); } catch { } }
                                }
                                vals.Add(v);
                            }
                            parrotMembers.Add($"{locName}.parrotPlatforms={count}:[{string.Join(",", vals)}]");
                            if (parrotUpgradesRaw.Count == 0 && vals.Count > 0)
                            {
                                parrotUpgradesRaw.AddRange(vals);
                                parrotDiag = $"from:{locName}.parrotPlatforms({count})";
                            }
                        }
                        catch { }
                    }

                    parrotExpress = parrotUpgradesRaw.Any(v => v > 0);
                    if (parrotUpgradesRaw.Count == 0) parrotDiag = "no-platforms-unlocked";
                }
                catch (Exception ex) { parrotDiag = "err:" + ex.GetType().Name; }
                var unlocks = new Dictionary<string, object>
                {
                    ["mine"] = new { unlocked = mineOpen, how = "春5日收到信后可进矿洞" },
                    ["bus"] = new { unlocked = Has("Pam_cc_Bus") || Has("ccVault"), how = "修好巴士（社区中心金库/Joja 42,500g）" },
                    ["sewer"] = new { unlocked = Has("HasRustyKey") || Has("OpenedSewer") || Has("CF_Sewer"), how = "博物馆捐60个古物获得生锈钥匙" },
                    ["secretWoods"] = new { unlocked = axeLevel >= 2, how = "升级到钢斧（砍大木桩）" },
                    ["skullCavern"] = new { unlocked = Has("HasSkullKey") || Has("HasUnlockedSkullDoor") || Has("skullCave"), how = "到达矿井底部120层获得头骨钥匙" },
                    ["island"] = new { unlocked = Has("willyBoatFixed"), how = "完成社区中心后找威利修船" },
                    ["railroad"] = new { unlocked = railroadOpen, how = "夏3日地震后开放" },
                    ["casino"] = new { unlocked = (Game1.player?.hasClubCard ?? false), how = "完成神秘的齐任务线获得会员卡" },
                    ["summit"] = new { unlocked = summit, how = "100%完美达成" },
                    ["mastery"] = new { unlocked = mastery, how = "钓鱼/采集/战斗/挖矿/耕种全10级" },
                    ["greenhouse"] = new { unlocked = Has("ccPantry"), how = "完成社区中心储藏室/Joja温室" },
                    ["communityCenter"] = new { unlocked = Has("ccIsComplete") || Has("ccDoorUnlock") || Has("Lewis_cc_Begin"), how = "完成刘易斯任务/献祭" },
                    ["cinema"] = new { unlocked = Has("ccMovieTheater") || Has("abandonedJojaMartAccessible"), how = "完成Joja路线/电影院" },
                    ["witchSwamp"] = new { unlocked = Has("HasDarkTalisman"), how = "完成黑暗护身符任务（法师）" },
                    ["townKey"] = new { unlocked = Has("HasTownKey"), how = "完成小镇钥匙任务（任意时段进居民家）" },
                    ["forestMagic"] = new { unlocked = Has("Lewis_cc_Begin") || Has("ccIntro"), how = "森林魔法（献祭功能解锁）" },
                    ["parrotExpress"] = new { unlocked = parrotExpress, how = "喂金核桃给岛上鹦鹉解锁快捷（姜岛→农场图腾/岛内传送）" },
                    ["parrotUpgradesRaw"] = parrotUpgradesRaw.ToArray(),
                    ["parrotDiag"] = parrotDiag,
                    ["parrotMembers"] = parrotMembers.ToArray(),
                };

                tcs.SetResult(new { ok = true, unlocks });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    /// <summary>
    /// GET /unlock_debug
    /// 校准用：dump 与解锁相关的真实游戏状态（mail flag / 工具等级 / 技能），
    /// 据此精确定位每个 /unlocks 判定该用哪个 flag（2026-08-14 不猜，实测校准）。
    /// </summary>
    private object HandleUnlockDebug()
    {
        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var tcs = new TaskCompletionSource<object>();
        EnqueueMainThread(() =>
        {
            try
            {
                var master = Game1.MasterPlayer;
                var relevant = new List<string>();
                if (master?.mailReceived != null)
                {
                    foreach (var m in master.mailReceived)
                    {
                        if (!string.IsNullOrEmpty(m))
                            relevant.Add(m);
                    }
                }
                relevant.Sort();

                var tools = new Dictionary<string, int>();
                foreach (var tname in new[] { "Axe", "Pickaxe", "Hoe", "Watering Can" })
                {
                    if (master?.getToolFromName(tname) is Tool tt)
                        tools[tname] = tt.UpgradeLevel;
                }

                tcs.SetResult(new
                {
                    ok = true,
                    relevantMail = relevant,
                    tools,
                    skills = new
                    {
                        farming = master?.farmingLevel.Value ?? 0,
                        mining = master?.miningLevel.Value ?? 0,
                        foraging = master?.foragingLevel.Value ?? 0,
                        fishing = master?.fishingLevel.Value ?? 0,
                        combat = master?.combatLevel.Value ?? 0,
                    },
                    date = $"{Game1.currentSeason} {Game1.dayOfMonth} 年{Game1.year}",
                });
            }
            catch (Exception ex)
            {
                tcs.SetResult(new { ok = false, error = ex.Message });
            }
        });
        return tcs.Task.GetAwaiter().GetResult();
    }

    // ── Walk-to routing ──

    /// <summary>
    /// POST /walk_to  { "location": "Town", "x": 20, "y": 30 }
    /// Walks to a tile on the specified map.
    /// If the target is on a different map, warps there first, then walks.
    /// Returns immediately; progress via /alerts (walk_completed).
    /// </summary>
    private object HandleWalkTo(HttpListenerContext ctx)
    {
        var p = ReadJson(ctx);
        var location = GetParam<string>(p, "location");
        var x = GetParamOr(p, "x", -1);
        var y = GetParamOr(p, "y", -1);

        if (!Context.IsWorldReady)
            throw new InvalidOperationException("World not ready");

        var farmer = Game1.player;
        // ⚠️ 2026-08-14：fromLoc 用 NameOrUniqueName（真实名），同地图判断才不会把小屋(显示名 Cabin)误判为跨地图
        var fromLoc = farmer.currentLocation.NameOrUniqueName ?? farmer.currentLocation.Name;

        // Resolve target coordinates
        if (x < 0 || y < 0)
        {
            x = farmer.TilePoint.X;
            y = farmer.TilePoint.Y;
        }

        // Cancel any ongoing movement
        _pathQueue = null;
        _walkRoute = null;

        // Same map → just walk
        if (string.Equals(fromLoc, location, StringComparison.OrdinalIgnoreCase))
        {
            _walkRoute = new List<WalkSegment> { new WalkSegment(location, x, y) };
            _walkSegIdx = 0;
            _walkSegmentStarted = false;
            _warpPending = false;
            return new { ok = true, action = "walk_to", destination = new { location, x, y }, segments = 1 };
        }

        // Different map: warp to a reliable entry point, then walk to destination.
        // Entry point = the target map's first incoming warp destination (targetX/Y is valid ground).
        int entryX = x, entryY = y;
        var targetLocation = Game1.getLocationFromName(location);
        if (targetLocation?.warps is { Count: > 0 })
        {
            // Use the FIRST outgoing warp's TARGET coords — these are always on valid ground
            // (source X/Y can be -1 or map edge, but target X/Y is where you actually appear)
            var firstWarp = targetLocation.warps[0];
            // Find a warp going INTO this map by checking all locations' warps
            foreach (var gloc in Game1.locations)
            {
                if (gloc == null || gloc.Name == location) continue;
                foreach (var w in gloc.warps)
                {
                    if (w.TargetName == location)
                    {
                        entryX = w.TargetX; entryY = w.TargetY;
                        goto foundEntry;
                    }
                }
            }
            // Fallback: if no incoming warp found, use center-ish of map
            entryX = targetLocation.Map.DisplayWidth / 64 / 2;
            entryY = targetLocation.Map.DisplayHeight / 64 / 2;
            foundEntry:;
        }
        EnqueueMainThread(() => { Game1.warpFarmer(ResolveWarpTarget(location), entryX, entryY, false); });
        _walkRoute = new List<WalkSegment> { new WalkSegment(location, x, y) };
        _walkSegIdx = 0; _walkSegmentStarted = false; _warpPending = true;
        EnqueueAlert("walk_started", $"Warp to {location} entry→walk ({x},{y})", "info", "walk");

        return new
        {
            ok = true,
            action = "walk_to",
            destination = new { location, x, y },
            warp = new { from = fromLoc, to = location }
        };
    }

    /// <summary>
    /// Lazy-build a map graph from all game locations' warp points.
    /// </summary>
    private void EnsureWarpGraph()
    {
        if (_warpGraph != null) return;
        _warpGraph = new Dictionary<string, List<WarpLink>>();

        foreach (var gloc in Game1.locations)
        {
            if (string.IsNullOrEmpty(gloc.Name)) continue;
            if (!_warpGraph.ContainsKey(gloc.Name))
                _warpGraph[gloc.Name] = new List<WarpLink>();

            foreach (var w in gloc.warps)
            {
                if (string.IsNullOrEmpty(w.TargetName)) continue;

                // Deduplicate: same (source → target) may appear on multiple adjacent tiles
                var existing = _warpGraph[gloc.Name].Any(
                    l => l.TargetLocation == w.TargetName && l.SourceX == w.X && l.SourceY == w.Y);
                if (!existing)
                {
                    _warpGraph[gloc.Name].Add(new WarpLink(w.TargetName, w.X, w.Y, w.TargetX, w.TargetY));
                }
            }
        }
    }

    /// <summary>
    /// BFS across maps to find the shortest map-name path.
    /// Returns null if no route exists.
    /// </summary>
    private List<string>? FindMapPath(string fromLocation, string toLocation)
    {
        if (fromLocation == toLocation)
            return new List<string> { fromLocation };

        EnsureWarpGraph();

        // Ensure the starting location is in the graph (handles building interiors)
        var playerLoc = Game1.player?.currentLocation;
        if (playerLoc != null && !_warpGraph!.ContainsKey(fromLocation))
        {
            _warpGraph[fromLocation] = new List<WarpLink>();
            foreach (var w in playerLoc.warps)
            {
                if (!string.IsNullOrEmpty(w.TargetName))
                    _warpGraph[fromLocation].Add(new WarpLink(w.TargetName, w.X, w.Y, w.TargetX, w.TargetY));
            }
        }

        var visited = new HashSet<string>(StringComparer.OrdinalIgnoreCase) { fromLocation };
        var queue = new Queue<(string location, List<string> path)>();
        queue.Enqueue((fromLocation, new List<string> { fromLocation }));

        while (queue.Count > 0)
        {
            var (current, path) = queue.Dequeue();

            if (!_warpGraph!.ContainsKey(current)) continue;

            foreach (var link in _warpGraph[current])
            {
                if (visited.Contains(link.TargetLocation)) continue;

                if (string.Equals(link.TargetLocation, toLocation, StringComparison.OrdinalIgnoreCase))
                {
                    path.Add(toLocation);
                    return path;
                }

                visited.Add(link.TargetLocation);
                var newPath = new List<string>(path) { link.TargetLocation };
                queue.Enqueue((link.TargetLocation, newPath));
            }
        }

        return null; // No path found
    }

    /// <summary>
    /// Build a full walk route from current location/tile to destination.
    /// Each WalkSegment is "(on this map, walk to this tile)".
    /// Intermediate segments target the warp tile; the final segment targets the user's destination.
    /// Returns null if no path exists between locations.
    /// </summary>
    private List<WalkSegment>? BuildWalkRoute(string fromLoc, Point fromTile, string toLoc, int toX, int toY)
    {
        if (string.Equals(fromLoc, toLoc, StringComparison.OrdinalIgnoreCase))
        {
            return new List<WalkSegment> { new WalkSegment(toLoc, toX, toY) };
        }

        var mapPath = FindMapPath(fromLoc, toLoc);
        if (mapPath == null) return null;

        var route = new List<WalkSegment>();

        // Segment 0: find the warp to the next map on the current location
        // Use the live warps list (handles building interiors not yet in the graph)
        WarpLink? firstLink = null;
        var playerLoc = Game1.player?.currentLocation;
        if (playerLoc != null && playerLoc.Name == fromLoc)
        {
            var warp = playerLoc.warps.FirstOrDefault(w =>
                string.Equals(w.TargetName, mapPath[1], StringComparison.OrdinalIgnoreCase));
            if (warp != null)
                firstLink = new WarpLink(warp.TargetName, warp.X, warp.Y, warp.TargetX, warp.TargetY);
        }
        // Fallback to graph
        firstLink ??= _warpGraph?.GetValueOrDefault(fromLoc)?.FirstOrDefault(w =>
            string.Equals(w.TargetLocation, mapPath[1], StringComparison.OrdinalIgnoreCase));
        if (firstLink == null) return null;
        route.Add(new WalkSegment(fromLoc, firstLink.SourceX, firstLink.SourceY));

        // Intermediate maps: walk from entry point to the next map's warp exit
        for (int i = 1; i < mapPath.Count - 1; i++)
        {
            var cur = mapPath[i];
            var next = mapPath[i + 1];
            if (_warpGraph == null || !_warpGraph.TryGetValue(cur, out var curLinks)) return null;

            var link = curLinks.FirstOrDefault(w =>
                string.Equals(w.TargetLocation, next, StringComparison.OrdinalIgnoreCase));
            if (link == null) return null;
            route.Add(new WalkSegment(cur, link.SourceX, link.SourceY));
        }

        // Final segment: walk to user's target tile on the destination map
        route.Add(new WalkSegment(toLoc, toX, toY));

        return route;
    }

    private Vector2 GetFacingTile(Farmer farmer)
    {
        int x = farmer.TilePoint.X;
        int y = farmer.TilePoint.Y;
        return farmer.FacingDirection switch
        {
            0 => new Vector2(x, y - 1),
            1 => new Vector2(x + 1, y),
            2 => new Vector2(x, y + 1),
            3 => new Vector2(x - 1, y),
            _ => new Vector2(x, y)
        };
    }

    // --- Helpers ---

    private void EnqueueMainThread(Action action)
    {
        lock (_queueLock)
        {
            _mainThreadQueue.Enqueue(action);
        }
    }

    /// <summary>
    /// Simple BFS pathfinding on the game map.
    /// </summary>
    private Queue<Point>? FindPath(GameLocation location, Point start, Point end)
    {
        if (start == end) return new Queue<Point>();

        // 先正常 BFS
        var direct = BfsTo(location, start, end);
        if (direct != null) return direct;

        // BFS 失败：终点不可走（床/家具/门）或路径被家具挡住（如床边电视卡位）
        // → 走到最近的可走邻居，而不是 BFS 失败让调用方传送闪现
        int[] ndx = { 0, 0, -1, 1 };
        int[] ndy = { -1, 1, 0, 0 };
        Queue<Point>? bestPath = null;
        var bestDist = int.MaxValue;
        for (int i = 0; i < 4; i++)
        {
            var cand = new Point(end.X + ndx[i], end.Y + ndy[i]);
            if (cand == start) return new Queue<Point>();
            if (!IsTilePassable(location, cand)) continue;
            var d = Math.Abs(start.X - cand.X) + Math.Abs(start.Y - cand.Y);
            if (d >= bestDist) continue;
            var p = BfsTo(location, start, cand);
            if (p != null) { bestPath = p; bestDist = d; }
        }
        return bestPath;
    }

    private Queue<Point>? BfsTo(GameLocation location, Point start, Point end)
    {
        var maxSteps = 5000;
        var visited = new HashSet<Point> { start };
        var queue = new Queue<(Point pos, List<Point> path)>();
        queue.Enqueue((start, new List<Point>()));

        int[] dx = { 0, 0, -1, 1 };
        int[] dy = { -1, 1, 0, 0 };

        while (queue.Count > 0 && maxSteps-- > 0)
        {
            var (pos, path) = queue.Dequeue();

            for (int i = 0; i < 4; i++)
            {
                var next = new Point(pos.X + dx[i], pos.Y + dy[i]);

                if (visited.Contains(next)) continue;
                if (!IsTilePassable(location, next)) continue;

                visited.Add(next);
                var newPath = new List<Point>(path) { next };

                if (next == end)
                    return new Queue<Point>(newPath);

                queue.Enqueue((next, newPath));
            }
        }

        // If no path found, return null (caller will fallback to direct walk)
        return null;
    }

    private bool IsTilePassable(GameLocation location, Point tile)
    {
        // Check map bounds
        if (tile.X < 0 || tile.Y < 0) return false;
        var mapWidth = location.Map.DisplayWidth / 64;
        var mapHeight = location.Map.DisplayHeight / 64;
        if (tile.X >= mapWidth || tile.Y >= mapHeight) return false;

        // Use the game's built-in passability check（只查地图图层，不查家具/物体）
        var tileVec = new Vector2(tile.X, tile.Y);
        if (!location.isTilePassable(tileVec)) return false;

        // 额外：家具/摆放物碰撞（室内床/桌子/箱子等 isTilePassable 不查，会穿墙）
        try
        {
            foreach (var f in location.furniture)
            {
                if (f == null) continue;
                int fx = (int)f.TileLocation.X, fy = (int)f.TileLocation.Y;
                if (tile.X >= fx && tile.X < fx + f.getTilesWide()
                    && tile.Y >= fy && tile.Y < fy + f.getTilesHigh())
                {
                    if (!f.isPassable()) return false;
                }
            }
            if (location.objects != null && location.objects.TryGetValue(tileVec, out var obj) && obj != null)
            {
                if (!obj.isPassable()) return false;
            }
        }
        catch { }
        return true;
    }

    /// <summary>
    /// Parse a hex color string ("RRGGBB" or "AARRGGBB", with or without #) to Color.
    /// Returns null on failure.
    /// </summary>
    private static Color? ParseColor(string? hex)
    {
        if (string.IsNullOrWhiteSpace(hex)) return null;
        hex = hex.TrimStart('#');
        if (hex.Length != 6 && hex.Length != 8) return null;

        try
        {
            uint val = Convert.ToUInt32(hex, 16);
            if (hex.Length == 6)
            {
                // RRGGBB → opaque
                byte r = (byte)((val >> 16) & 0xFF);
                byte g = (byte)((val >> 8) & 0xFF);
                byte b = (byte)(val & 0xFF);
                return new Color(r, g, b);
            }
            else
            {
                // AARRGGBB
                byte a = (byte)((val >> 24) & 0xFF);
                byte r = (byte)((val >> 16) & 0xFF);
                byte g = (byte)((val >> 8) & 0xFF);
                byte b = (byte)(val & 0xFF);
                return new Color(r, g, b, a);
            }
        }
        catch { return null; }
    }
}
