namespace NagiBridge;

public class ModConfig
{
    public string Mode { get; set; } = "cc";
    public string ChannelServerUrl { get; set; } = "http://localhost:9000/chat";
    public string ApiProvider { get; set; } = "claude";
    public string ApiUrl { get; set; } = "";
    public string ApiKey { get; set; } = "";
    public string Model { get; set; } = "claude-sonnet-4-6-20250514";
    public string SystemPrompt { get; set; } = "You are a friendly AI companion in Stardew Valley. You chat casually with the player about farm life, give tips, and keep them company. Keep responses short (1-3 sentences) since this is in-game chat.";
    public int MaxHistoryMessages { get; set; } = 20;

    // ⚠️ 2026-10-14：窗口尺寸默认 **0 = 不干预**（以前是 1280×720）。
    //    旧默认的后果：**别人机器上不清 config.json 就会被硬设成 1280×720**
    //    （大屏偏小、小屏可能顶边；不会崩，但不"自适应"）—— 典型的"只有换机器才看得见"的坑，
    //    跟 launcher_check 那次的 mcp<2 是同一类（本机永远复现不出来）。
    //    ModEntry.cs:1108 的守卫只在 **两个都 > 0** 时才 ApplyChanges，所以 0 就是干净的"我不碰窗口"。
    //    ⚠️ 已经生成过 config.json 的老用户：文件里那对 1280/720 是**旧默认写进去的**，升上来仍会被硬设 ——
    //       要他们自己改成 0（README 与发布说明里都写了）。
    public int WindowWidth { get; set; } = 0;
    public int WindowHeight { get; set; } = 0;
}
