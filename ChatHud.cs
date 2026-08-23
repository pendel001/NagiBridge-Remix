using System;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Xna.Framework;
using Microsoft.Xna.Framework.Graphics;
using Microsoft.Xna.Framework.Input;
using StardewModdingAPI;
using StardewValley;

namespace NagiBridge;

public class ChatMessage
{
    public string Sender { get; set; } = "";
    public string Text { get; set; } = "";
    public DateTime Time { get; set; } = DateTime.Now;
    public bool IsPlayer => Sender != "Nagi" && Sender != "System";
}

public enum ChatState { ModeSelect, NameSetup, ApiSetup, Chat }

public class ChatHud
{
    private readonly List<ChatMessage> _messages = new();
    private bool _isOpen;
    private ChatState _state = ChatState.ModeSelect;
    private bool _modeChosen;

    // Chat
    private string _inputText = "";
    private int _scrollOffset;
    private int _cursorBlink;
    private const int MaxHistory = 50;
    private const int VisibleMessages = 10;
    private const int HudVisibleMessages = 2;

    // Mode select
    private int _modeIndex; // 0=API, 1=Channel
    private int _selectedMode; // remember which mode was picked

    // Name setup
    private string _nameInput = "Nagi";

    // API setup
    private string _apiKeyInput = "";
    private string _apiUrlInput = "https://api.anthropic.com/v1/messages";
    private int _setupField; // 0=key, 1=url
    private string _connectStatus = "";

    private KeyboardState _prevKeyState;
    private MouseState _prevMouseState;
    private bool _textInputHooked;

    [DllImport("user32.dll")] private static extern bool OpenClipboard(IntPtr h);
    [DllImport("user32.dll")] private static extern IntPtr GetClipboardData(uint f);
    [DllImport("user32.dll")] private static extern bool CloseClipboard();
    [DllImport("kernel32.dll")] private static extern IntPtr GlobalLock(IntPtr h);
    [DllImport("kernel32.dll")] private static extern bool GlobalUnlock(IntPtr h);

    private static string? GetClipboardText()
    {
        if (!OpenClipboard(IntPtr.Zero)) return null;
        try
        {
            var handle = GetClipboardData(13); // CF_UNICODETEXT
            if (handle == IntPtr.Zero) return null;
            var ptr = GlobalLock(handle);
            if (ptr == IntPtr.Zero) return null;
            try { return Marshal.PtrToStringUni(ptr); }
            finally { GlobalUnlock(handle); }
        }
        finally { CloseClipboard(); }
    }

    private readonly IMonitor _monitor;
    private readonly Action<string>? _onSend;
    private readonly Action<string, string>? _onApiConfigured;
    private readonly Action? _onChannelSelected;

    public ChatHud(IMonitor monitor, Action<string>? onSend = null, Action<string, string>? onApiConfigured = null, Action? onChannelSelected = null)
    {
        _monitor = monitor;
        _onSend = onSend;
        _onApiConfigured = onApiConfigured;
        _onChannelSelected = onChannelSelected;
    }

    public bool IsOpen => _isOpen;
    public ChatState State => _state;
    public string AiDisplayName => _nameInput;

    public void SetInitialState(string mode, string apiKey, string apiUrl = "")
    {
        if (!string.IsNullOrEmpty(apiKey))
            _apiKeyInput = apiKey;
        if (!string.IsNullOrEmpty(apiUrl))
            _apiUrlInput = apiUrl;

        if (mode.Equals("cc", StringComparison.OrdinalIgnoreCase))
        {
            _state = ChatState.Chat;
            _modeChosen = true;
        }
    }

    public void AddMessage(string sender, string text)
    {
        _messages.Add(new ChatMessage { Sender = sender, Text = text });
        if (_messages.Count > MaxHistory)
            _messages.RemoveAt(0);
        _scrollOffset = 0;
    }

    private void UpdateTextInputHook()
    {
        if (_isOpen && _state == ChatState.Chat && !_textInputHooked)
        {
            Game1.game1.Window.TextInput += OnTextInput;
            _textInputHooked = true;
        }
        else if ((!_isOpen || _state != ChatState.Chat) && _textInputHooked)
        {
            Game1.game1.Window.TextInput -= OnTextInput;
            _textInputHooked = false;
        }
    }

    private void UpdateSetupTextInputHook()
    {
        bool needHook = _isOpen && (_state == ChatState.ApiSetup || _state == ChatState.NameSetup);
        if (needHook && !_textInputHooked)
        {
            Game1.game1.Window.TextInput += OnSetupTextInput;
            _textInputHooked = true;
        }
        else if (!needHook && _textInputHooked)
        {
            Game1.game1.Window.TextInput -= OnSetupTextInput;
            _textInputHooked = false;
        }
    }

    private void OnTextInput(object? sender, TextInputEventArgs e)
    {
        if (!_isOpen || _state != ChatState.Chat) return;
        char c = e.Character;
        if (c == '\r' || c == '\n' || c == '\t' || c == '\x1b') return;
        if (c == '\b')
        {
            if (_inputText.Length > 0)
                _inputText = _inputText[..^1];
            return;
        }
        if (_inputText.Length < 200)
            _inputText += c;
    }

    private void OnSetupTextInput(object? sender, TextInputEventArgs e)
    {
        if (!_isOpen) return;
        char c = e.Character;
        if (c == '\r' || c == '\n' || c == '\x1b') return;

        if (_state == ChatState.NameSetup)
        {
            if (c == '\b')
            {
                if (_nameInput.Length > 0) _nameInput = _nameInput[..^1];
            }
            else if (_nameInput.Length < 30)
                _nameInput += c;
            return;
        }

        if (_state != ChatState.ApiSetup) return;
        if (c == '\t') return;

        ref string field = ref _apiKeyInput;
        if (_setupField == 1) field = ref _apiUrlInput;

        if (c == '\b')
        {
            if (field.Length > 0)
                field = field[..^1];
            return;
        }
        if (field.Length < 300)
            field += c;
    }

    public void Update()
    {
        var keyState = Keyboard.GetState();
        var mouseState = Mouse.GetState();

        // Toggle open/close with OemTilde
        if (keyState.IsKeyDown(Keys.OemTilde) && !_prevKeyState.IsKeyDown(Keys.OemTilde))
        {
            if (!_isOpen && Game1.activeClickableMenu == null)
            {
                _isOpen = true;
                _scrollOffset = 0;
                if (!_modeChosen)
                    _state = ChatState.ModeSelect;
                if (_state == ChatState.Chat)
                    UpdateTextInputHook();
                else if (_state == ChatState.ApiSetup || _state == ChatState.NameSetup)
                    UpdateSetupTextInputHook();
            }
            else if (_isOpen)
            {
                _isOpen = false;
                _inputText = "";
                UpdateTextInputHook();
                UpdateSetupTextInputHook();
            }
        }

        if (_isOpen)
        {
            switch (_state)
            {
                case ChatState.ModeSelect:
                    UpdateModeSelect(keyState);
                    break;
                case ChatState.NameSetup:
                    UpdateNameSetup(keyState);
                    break;
                case ChatState.ApiSetup:
                    UpdateApiSetup(keyState);
                    break;
                case ChatState.Chat:
                    UpdateChat(keyState, mouseState);
                    break;
            }
        }

        _cursorBlink = (_cursorBlink + 1) % 60;
        _prevKeyState = keyState;
        _prevMouseState = mouseState;
    }

    private void UpdateModeSelect(KeyboardState keyState)
    {
        if (keyState.IsKeyDown(Keys.Up) && !_prevKeyState.IsKeyDown(Keys.Up))
            _modeIndex = 0;
        if (keyState.IsKeyDown(Keys.Down) && !_prevKeyState.IsKeyDown(Keys.Down))
            _modeIndex = 1;

        if (keyState.IsKeyDown(Keys.Enter) && !_prevKeyState.IsKeyDown(Keys.Enter))
        {
            _selectedMode = _modeIndex;
            _state = ChatState.NameSetup;
            UpdateSetupTextInputHook();
        }
    }

    private void UpdateNameSetup(KeyboardState keyState)
    {
        bool ctrl = keyState.IsKeyDown(Keys.LeftControl) || keyState.IsKeyDown(Keys.RightControl);
        if (ctrl && keyState.IsKeyDown(Keys.V) && !_prevKeyState.IsKeyDown(Keys.V))
        {
            var clip = GetClipboardText()?.Trim();
            if (!string.IsNullOrEmpty(clip) && _nameInput.Length + clip.Length <= 30)
                _nameInput += clip;
        }

        if (keyState.IsKeyDown(Keys.Enter) && !_prevKeyState.IsKeyDown(Keys.Enter))
        {
            if (!string.IsNullOrWhiteSpace(_nameInput))
            {
                if (_textInputHooked)
                {
                    Game1.game1.Window.TextInput -= OnSetupTextInput;
                    _textInputHooked = false;
                }
                if (_selectedMode == 0) // API
                {
                    _state = ChatState.ApiSetup;
                    _setupField = 0;
                    UpdateSetupTextInputHook();
                }
                else // Channel
                {
                    _state = ChatState.Chat;
                    _modeChosen = true;
                    _onChannelSelected?.Invoke();
                    UpdateTextInputHook();
                    AddMessage("System", "Channel mode. Connected.");
                }
            }
        }

        if (keyState.IsKeyDown(Keys.Escape) && !_prevKeyState.IsKeyDown(Keys.Escape))
        {
            if (_textInputHooked)
            {
                Game1.game1.Window.TextInput -= OnSetupTextInput;
                _textInputHooked = false;
            }
            _state = ChatState.ModeSelect;
        }
    }

    private void UpdateApiSetup(KeyboardState keyState)
    {
        if (keyState.IsKeyDown(Keys.Tab) && !_prevKeyState.IsKeyDown(Keys.Tab))
            _setupField = (_setupField + 1) % 2;

        bool ctrl = keyState.IsKeyDown(Keys.LeftControl) || keyState.IsKeyDown(Keys.RightControl);
        if (ctrl && keyState.IsKeyDown(Keys.V) && !_prevKeyState.IsKeyDown(Keys.V))
        {
            var clip = GetClipboardText()?.Trim();
            if (!string.IsNullOrEmpty(clip))
            {
                if (_setupField == 0) _apiKeyInput += clip;
                else _apiUrlInput += clip;
            }
        }

        if (keyState.IsKeyDown(Keys.Enter) && !_prevKeyState.IsKeyDown(Keys.Enter))
        {
            if (!string.IsNullOrWhiteSpace(_apiKeyInput))
            {
                // Unhook setup input, switch to chat
                if (_textInputHooked)
                {
                    Game1.game1.Window.TextInput -= OnSetupTextInput;
                    _textInputHooked = false;
                }
                _onApiConfigured?.Invoke(_apiKeyInput.Trim(), _apiUrlInput.Trim());
                _state = ChatState.Chat;
                _modeChosen = true;
                UpdateTextInputHook();
                AddMessage("System", "API connected.");
            }
        }

        if (keyState.IsKeyDown(Keys.Escape) && !_prevKeyState.IsKeyDown(Keys.Escape))
        {
            if (_textInputHooked)
            {
                Game1.game1.Window.TextInput -= OnSetupTextInput;
                _textInputHooked = false;
            }
            _state = ChatState.ModeSelect;
        }
    }

    private void UpdateChat(KeyboardState keyState, MouseState mouseState)
    {
        // Tab = switch mode (only when input is empty)
        if (keyState.IsKeyDown(Keys.Tab) && !_prevKeyState.IsKeyDown(Keys.Tab)
            && string.IsNullOrEmpty(_inputText))
        {
            if (_textInputHooked)
            {
                Game1.game1.Window.TextInput -= OnTextInput;
                _textInputHooked = false;
            }
            _state = ChatState.ModeSelect;
            _modeChosen = false;
            return;
        }

        bool ctrl = keyState.IsKeyDown(Keys.LeftControl) || keyState.IsKeyDown(Keys.RightControl);
        if (ctrl && keyState.IsKeyDown(Keys.V) && !_prevKeyState.IsKeyDown(Keys.V))
        {
            var clip = GetClipboardText()?.Trim();
            if (!string.IsNullOrEmpty(clip))
                _inputText += clip;
        }

        if (keyState.IsKeyDown(Keys.Enter) && !_prevKeyState.IsKeyDown(Keys.Enter))
        {
            if (!string.IsNullOrWhiteSpace(_inputText))
            {
                var text = _inputText.Trim();
                AddMessage(Game1.player?.Name ?? "You", text);
                _onSend?.Invoke(text);
                _inputText = "";
            }
        }

        // Scroll
        int scrollDelta = mouseState.ScrollWheelValue - _prevMouseState.ScrollWheelValue;
        if (scrollDelta > 0 && _scrollOffset < _messages.Count - VisibleMessages)
            _scrollOffset++;
        else if (scrollDelta < 0 && _scrollOffset > 0)
            _scrollOffset--;
    }

    // === DRAWING ===

    public void DrawHud(SpriteBatch b)
    {
        if (_isOpen || _messages.Count == 0) return;

        var recent = _messages.TakeLast(HudVisibleMessages).ToList();
        var font = Game1.smallFont;
        int toolbarHeight = 100;
        int x = 20;
        int maxWidth = 400;

        int totalHeight = 0;
        foreach (var msg in recent)
        {
            string d = $"{msg.Sender}: {msg.Text}";
            var w = WrapText(font, d, maxWidth);
            totalHeight += (int)font.MeasureString(w).Y + 12;
        }
        int y = Game1.viewport.Height - toolbarHeight - totalHeight - 8;

        foreach (var msg in recent)
        {
            string display = $"{msg.Sender}: {msg.Text}";
            var wrapped = WrapText(font, display, maxWidth);
            var size = font.MeasureString(wrapped);

            DrawBox(b, x - 8, y - 4, (int)size.X + 16, (int)size.Y + 8, 0.6f);
            b.DrawString(font, wrapped, new Vector2(x, y),
                msg.IsPlayer ? Color.White : new Color(126, 184, 218),
                0f, Vector2.Zero, 1f, SpriteEffects.None, 1f);
            y += (int)size.Y + 12;
        }
    }

    public void DrawPanel(SpriteBatch b)
    {
        if (!_isOpen) return;

        switch (_state)
        {
            case ChatState.ModeSelect:
                DrawModeSelect(b);
                break;
            case ChatState.NameSetup:
                DrawNameSetup(b);
                break;
            case ChatState.ApiSetup:
                DrawApiSetup(b);
                break;
            case ChatState.Chat:
                DrawChatPanel(b);
                break;
        }
    }

    private void DrawModeSelect(SpriteBatch b)
    {
        var font = Game1.smallFont;
        int panelWidth = 340;
        int panelHeight = 160;
        int panelX = 16;
        int panelY = Game1.viewport.Height - panelHeight - 100;

        DrawBox(b, panelX, panelY, panelWidth, panelHeight, 0.9f);

        b.DrawString(font, "Nagi Chat", new Vector2(panelX + 16, panelY + 12), Color.Gold,
            0f, Vector2.Zero, 1f, SpriteEffects.None, 1f);

        int optY = panelY + 48;
        string[] options = { "API Mode", "Channel Mode" };
        for (int i = 0; i < options.Length; i++)
        {
            bool selected = i == _modeIndex;
            string prefix = selected ? "> " : "  ";
            var color = selected ? Color.Yellow : Color.White;
            b.DrawString(font, prefix + options[i], new Vector2(panelX + 24, optY),
                color, 0f, Vector2.Zero, 1f, SpriteEffects.None, 1f);
            optY += 32;
        }

        b.DrawString(font, "Up/Down = Select   Enter = OK",
            new Vector2(panelX + 16, panelY + panelHeight - 24),
            Color.Gray, 0f, Vector2.Zero, 0.7f, SpriteEffects.None, 1f);
    }

    private void DrawNameSetup(SpriteBatch b)
    {
        var font = Game1.smallFont;
        int panelWidth = 340;
        int panelHeight = 130;
        int panelX = 16;
        int panelY = Game1.viewport.Height - panelHeight - 100;

        DrawBox(b, panelX, panelY, panelWidth, panelHeight, 0.9f);

        string modeLabel = _selectedMode == 0 ? "API" : "Channel";
        b.DrawString(font, $"Display Name ({modeLabel})", new Vector2(panelX + 16, panelY + 12), Color.Gold,
            0f, Vector2.Zero, 1f, SpriteEffects.None, 1f);

        int fieldY = panelY + 50;
        DrawBox(b, panelX + 16, fieldY, panelWidth - 32, 28, 0.95f);
        string cursor = (_cursorBlink < 30) ? "|" : "";
        b.DrawString(font, _nameInput + cursor, new Vector2(panelX + 22, fieldY + 4), Color.White,
            0f, Vector2.Zero, 1f, SpriteEffects.None, 1f);

        b.DrawString(font, "Enter = OK   Esc = Back",
            new Vector2(panelX + 16, panelY + panelHeight - 24),
            Color.Gray, 0f, Vector2.Zero, 0.7f, SpriteEffects.None, 1f);
    }

    private void DrawApiSetup(SpriteBatch b)
    {
        var font = Game1.smallFont;
        int panelWidth = 460;
        int panelHeight = 200;
        int panelX = 16;
        int panelY = Game1.viewport.Height - panelHeight - 100;

        DrawBox(b, panelX, panelY, panelWidth, panelHeight, 0.9f);

        b.DrawString(font, "API Setup", new Vector2(panelX + 16, panelY + 12), Color.Gold,
            0f, Vector2.Zero, 1f, SpriteEffects.None, 1f);

        string cursor = (_cursorBlink < 30) ? "|" : "";
        int fieldY = panelY + 48;

        // API Key
        var keyColor = _setupField == 0 ? Color.Yellow : Color.Gray;
        b.DrawString(font, "API Key:", new Vector2(panelX + 16, fieldY), keyColor,
            0f, Vector2.Zero, 1f, SpriteEffects.None, 1f);
        fieldY += 24;
        DrawBox(b, panelX + 16, fieldY, panelWidth - 32, 28, 0.95f);
        string keyDisplay;
        if (_apiKeyInput.Length > 4)
            keyDisplay = new string('*', Math.Min(_apiKeyInput.Length - 4, 20)) + _apiKeyInput[^4..];
        else
            keyDisplay = _apiKeyInput;
        if (_setupField == 0) keyDisplay += cursor;
        b.DrawString(font, keyDisplay, new Vector2(panelX + 22, fieldY + 4), Color.White,
            0f, Vector2.Zero, 1f, SpriteEffects.None, 1f);

        fieldY += 38;

        // URL
        var urlColor = _setupField == 1 ? Color.Yellow : Color.Gray;
        b.DrawString(font, "URL:", new Vector2(panelX + 16, fieldY), urlColor,
            0f, Vector2.Zero, 1f, SpriteEffects.None, 1f);
        fieldY += 24;
        DrawBox(b, panelX + 16, fieldY, panelWidth - 32, 28, 0.95f);
        string urlDisplay = _apiUrlInput.Length > 45
            ? "..." + _apiUrlInput[^42..] : _apiUrlInput;
        if (_setupField == 1) urlDisplay += cursor;
        b.DrawString(font, urlDisplay, new Vector2(panelX + 22, fieldY + 4), Color.White,
            0f, Vector2.Zero, 1f, SpriteEffects.None, 1f);

        b.DrawString(font, "Tab = Switch   Enter = Connect   Esc = Back",
            new Vector2(panelX + 16, panelY + panelHeight - 24),
            Color.Gray, 0f, Vector2.Zero, 0.7f, SpriteEffects.None, 1f);
    }

    private void DrawChatPanel(SpriteBatch b)
    {
        var font = Game1.smallFont;
        int panelWidth = 460;
        int lineHeight = 26;
        int padding = 12;

        var visible = GetVisibleMessages();
        int msgLines = 0;
        foreach (var msg in visible)
        {
            string display = $"[{msg.Time:HH:mm}] {msg.Sender}: {msg.Text}";
            var wrapped = WrapText(font, display, panelWidth - 32);
            msgLines += wrapped.Split('\n').Length;
        }
        msgLines = Math.Max(msgLines, 3);

        int titleHeight = 30;
        int msgAreaHeight = msgLines * lineHeight + padding;
        int inputHeight = 36;
        int helpHeight = 18;
        int panelHeight = titleHeight + msgAreaHeight + inputHeight + helpHeight + padding;

        int panelX = 16;
        int panelY = Game1.viewport.Height - panelHeight - 100;

        DrawBox(b, panelX, panelY, panelWidth, panelHeight, 0.88f);

        b.DrawString(font, "Nagi Chat", new Vector2(panelX + 16, panelY + 8), Color.Gold,
            0f, Vector2.Zero, 1f, SpriteEffects.None, 1f);

        int msgAreaTop = panelY + titleHeight;
        int drawY = msgAreaTop;

        foreach (var msg in visible)
        {
            string display = $"[{msg.Time:HH:mm}] {msg.Sender}: {msg.Text}";
            var wrapped = WrapText(font, display, panelWidth - 32);
            var lines = wrapped.Split('\n');
            var color = msg.IsPlayer ? Color.White : new Color(126, 184, 218);

            foreach (var line in lines)
            {
                if (drawY + lineHeight > msgAreaTop + msgAreaHeight) break;
                b.DrawString(font, line, new Vector2(panelX + 16, drawY),
                    color, 0f, Vector2.Zero, 1f, SpriteEffects.None, 1f);
                drawY += lineHeight;
            }
        }

        if (_messages.Count > VisibleMessages)
        {
            string scrollInfo = $"{_scrollOffset}/{_messages.Count}";
            b.DrawString(font, scrollInfo, new Vector2(panelX + panelWidth - 80, panelY + 8),
                Color.Gray, 0f, Vector2.Zero, 0.8f, SpriteEffects.None, 1f);
        }

        int inputY = panelY + panelHeight - inputHeight - helpHeight;
        DrawBox(b, panelX + 8, inputY, panelWidth - 16, 30, 0.95f);

        string cursorChar = (_cursorBlink < 30) ? "|" : "";
        b.DrawString(font, _inputText + cursorChar, new Vector2(panelX + 16, inputY + 5), Color.White,
            0f, Vector2.Zero, 1f, SpriteEffects.None, 1f);

        b.DrawString(font, "` = Close  Enter = Send  Tab = Switch Mode",
            new Vector2(panelX + 16, panelY + panelHeight - helpHeight),
            Color.Gray, 0f, Vector2.Zero, 0.7f, SpriteEffects.None, 1f);
    }

    private List<ChatMessage> GetVisibleMessages()
    {
        if (_messages.Count == 0) return new();
        int start = Math.Max(0, _messages.Count - VisibleMessages - _scrollOffset);
        int count = Math.Min(VisibleMessages, _messages.Count - start);
        return _messages.GetRange(start, count);
    }

    private static void DrawBox(SpriteBatch b, int x, int y, int w, int h, float alpha)
    {
        var pixel = Game1.staminaRect;
        b.Draw(pixel, new Microsoft.Xna.Framework.Rectangle(x, y, w, h), Color.Black * alpha);
        b.Draw(pixel, new Microsoft.Xna.Framework.Rectangle(x, y, w, 1), Color.Gray * 0.8f);
        b.Draw(pixel, new Microsoft.Xna.Framework.Rectangle(x, y + h - 1, w, 1), Color.Gray * 0.8f);
        b.Draw(pixel, new Microsoft.Xna.Framework.Rectangle(x, y, 1, h), Color.Gray * 0.8f);
        b.Draw(pixel, new Microsoft.Xna.Framework.Rectangle(x + w - 1, y, 1, h), Color.Gray * 0.8f);
    }

    private static string WrapText(SpriteFont font, string text, int maxWidth)
    {
        if (string.IsNullOrEmpty(text)) return "";
        if (font.MeasureString(text).X <= maxWidth)
            return text;

        var sb = new StringBuilder();
        float lineWidth = 0;

        foreach (char c in text)
        {
            if (c == '\n')
            {
                sb.AppendLine();
                lineWidth = 0;
                continue;
            }
            float charWidth = font.MeasureString(c.ToString()).X;
            if (lineWidth + charWidth > maxWidth && lineWidth > 0)
            {
                sb.AppendLine();
                lineWidth = 0;
            }
            sb.Append(c);
            lineWidth += charWidth;
        }
        return sb.ToString();
    }
}
