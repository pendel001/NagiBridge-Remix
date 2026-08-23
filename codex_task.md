Add a Prairie King (JotPK) minigame bot to ModEntry.cs. Read ModEntry.cs first to understand the code patterns.

Add two new endpoints in the path switch (~line 386):
1. "/minigame/state" => HandleMinigameState() - GET, read AbigailGame state via reflection
2. "/minigame/bot" => HandleMinigameBot(ctx) - POST, {action: "start"|"stop"|"status"}

Add bot tick logic in OnUpdateTicked: when _minigameBotActive and Game1.currentMinigame is AbigailGame, run TickPrairieKingBot every frame.

TickPrairieKingBot algorithm using potential fields:
- Cache FieldInfo as static fields for performance
- Movement: dodge enemy bullets, keep distance from monsters, collect powerups when safe
- Shooting: always shoot toward nearest monster, 8-way direction
- Inject input by setting playerMovementDirections and playerShootingDirections via reflection
- Handle edge cases: shopping, death, between waves

Key AbigailGame info:
- Full type: StardewValley.Minigames.AbigailGame
- All fields private, use reflection. "monsters" field is STATIC.
- Map: 16x16 tiles, 48px each. Player bounds: ~48 to ~720px
- Directions: 0=up,1=right,2=down,3=left
- CowboyMonster: position(Rectangle), health(int), type(int), speed(int)
- CowboyBullet: position(Point), motion(Point), damage(int)
- CowboyPowerup: which(int), position(Point)

After writing, build with: dotnet build -c Release
Fix compilation errors until build succeeds.
