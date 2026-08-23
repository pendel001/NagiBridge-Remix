using System;
using System.Collections.Generic;
using System.IO;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;

namespace NagiBridge;

public class LlmClient
{
    private readonly ModConfig _config;
    private readonly HttpClient _http = new();
    private readonly List<ChatTurn> _history = new();
    private readonly string _historyPath;

    private record ChatTurn(string Role, string Content);

    public LlmClient(ModConfig config, string? modPath = null)
    {
        _config = config;
        _historyPath = modPath != null
            ? Path.Combine(modPath, "chat_history.json")
            : Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "Mods", "NagiBridge", "chat_history.json");
        LoadHistory();
    }

    private void LoadHistory()
    {
        try
        {
            if (File.Exists(_historyPath))
            {
                var json = File.ReadAllText(_historyPath, Encoding.UTF8);
                var entries = JsonSerializer.Deserialize<List<HistoryEntry>>(json);
                if (entries != null)
                {
                    _history.Clear();
                    foreach (var e in entries)
                        _history.Add(new ChatTurn(e.role, e.content));
                }
            }
        }
        catch { }
    }

    private void SaveHistory()
    {
        try
        {
            var entries = new List<HistoryEntry>();
            foreach (var h in _history)
                entries.Add(new HistoryEntry { role = h.Role, content = h.Content });
            var json = JsonSerializer.Serialize(entries, new JsonSerializerOptions { WriteIndented = true });
            File.WriteAllText(_historyPath, json, Encoding.UTF8);
        }
        catch { }
    }

    private class HistoryEntry
    {
        public string role { get; set; } = "";
        public string content { get; set; } = "";
    }

    public async Task<string> SendAsync(string userMessage)
    {
        if (string.IsNullOrEmpty(_config.ApiKey))
            return "[No API key - paste in API Setup]";

        _history.Add(new ChatTurn("user", userMessage));
        if (_history.Count > _config.MaxHistoryMessages)
            _history.RemoveAt(0);

        try
        {
            var customUrl = string.IsNullOrEmpty(_config.ApiUrl) ? null : _config.ApiUrl;
            var response = _config.ApiProvider.ToLower() switch
            {
                "claude" or "anthropic" => await CallClaude(customUrl),
                "deepseek" => await CallOpenAICompatible(customUrl ?? "https://api.deepseek.com/v1/chat/completions"),
                "openai" => await CallOpenAICompatible(customUrl ?? "https://api.openai.com/v1/chat/completions"),
                _ => await CallOpenAICompatible(customUrl ?? "https://api.openai.com/v1/chat/completions")
            };

            _history.Add(new ChatTurn("assistant", response));
            if (_history.Count > _config.MaxHistoryMessages)
                _history.RemoveAt(0);

            SaveHistory();
            return response;
        }
        catch (Exception ex)
        {
            return $"[Error: {ex.Message}]";
        }
    }

    private async Task<string> CallClaude(string? customUrl = null)
    {
        var messages = new List<object>();
        foreach (var turn in _history)
            messages.Add(new { role = turn.Role, content = turn.Content });

        var body = new
        {
            model = _config.Model,
            max_tokens = 300,
            system = _config.SystemPrompt,
            messages
        };

        var request = new HttpRequestMessage(HttpMethod.Post, customUrl ?? "https://api.anthropic.com/v1/messages");
        request.Headers.Add("x-api-key", _config.ApiKey);
        request.Headers.Add("anthropic-version", "2023-06-01");
        request.Content = new StringContent(JsonSerializer.Serialize(body), Encoding.UTF8, "application/json");

        var resp = await _http.SendAsync(request);
        var json = await resp.Content.ReadAsStringAsync();

        if (!resp.IsSuccessStatusCode)
            return $"[API {(int)resp.StatusCode}: {TryExtractError(json)}]";

        using var doc = JsonDocument.Parse(json);
        var content = doc.RootElement.GetProperty("content");
        if (content.GetArrayLength() > 0)
            return content[0].GetProperty("text").GetString() ?? "";
        return "[Empty response]";
    }

    private async Task<string> CallOpenAICompatible(string endpoint)
    {
        var messages = new List<object>();
        messages.Add(new { role = "system", content = _config.SystemPrompt });
        foreach (var turn in _history)
            messages.Add(new { role = turn.Role, content = turn.Content });

        var body = new
        {
            model = _config.Model,
            max_tokens = 300,
            messages
        };

        var request = new HttpRequestMessage(HttpMethod.Post, endpoint);
        request.Headers.Add("Authorization", $"Bearer {_config.ApiKey}");
        request.Content = new StringContent(JsonSerializer.Serialize(body), Encoding.UTF8, "application/json");

        var resp = await _http.SendAsync(request);
        var json = await resp.Content.ReadAsStringAsync();

        if (!resp.IsSuccessStatusCode)
            return $"[API {(int)resp.StatusCode}: {TryExtractError(json)}]";

        using var doc = JsonDocument.Parse(json);
        var choices = doc.RootElement.GetProperty("choices");
        if (choices.GetArrayLength() > 0)
            return choices[0].GetProperty("message").GetProperty("content").GetString() ?? "";
        return "[Empty response]";
    }

    private static string TryExtractError(string json)
    {
        try
        {
            using var doc = JsonDocument.Parse(json);
            if (doc.RootElement.TryGetProperty("error", out var err))
            {
                if (err.ValueKind == JsonValueKind.Object && err.TryGetProperty("message", out var msg))
                    return msg.GetString() ?? "unknown";
                return err.GetString() ?? "unknown";
            }
        }
        catch { }
        return json.Length > 100 ? json[..100] : json;
    }
}
