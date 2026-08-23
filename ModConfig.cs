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
    public int WindowWidth { get; set; } = 1280;
    public int WindowHeight { get; set; } = 720;
}
