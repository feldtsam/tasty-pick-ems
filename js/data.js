// ============================================================
// TASTY PICK EMS — MOCK DATA
// Replace any section below with real API data or CSV imports.
// Each object shape is the contract the UI expects.
// ============================================================

const DATA = {

  // ── 1. BEST HR ENVIRONMENTS ─────────────────────────────────
  ballparks: [
    {
      park: "Coors Field",
      team: "COL",
      hrFactor: 1.38,
      wind: "Out to RF · 12 mph",
      insight: "Thin air + wind out. Best HR environment in MLB today."
    },
    {
      park: "Great American Ball Park",
      team: "CIN",
      hrFactor: 1.29,
      wind: "Out to LF/CF · 9 mph",
      insight: "Short RF porch + wind carrying. RHH heaven today."
    },
    {
      park: "Yankee Stadium",
      team: "NYY",
      hrFactor: 1.22,
      wind: "Out to RF · 8 mph",
      insight: "Short right-field porch. LHH advantage. Wind out this afternoon."
    },
    {
      park: "Citizens Bank Park",
      team: "PHI",
      hrFactor: 1.19,
      wind: "Neutral · 6 mph",
      insight: "Live air. Top-5 power park. Good across the board."
    }
  ],

  // ── 2. TARGETABLE PITCHERS ───────────────────────────────────
  pitchers: [
    {
      name: "Luis Severino",
      team: "OAK",
      opponent: "HOU",
      handedness: "RHP",
      era: 5.82,
      hrPer9: 2.1,
      insight: "Career-high FB%. Gets lit up by power bats. Houston lineup feasts up in the zone.",
      tiktokHook: "Nobody is talking about Severino today. 5.82 ERA. 2.1 HR per 9. Houston bats are LIVE. 🔥 #TastyPickEms",
      tier: "Primary Target"
    },
    {
      name: "Matthew Boyd",
      team: "SEA",
      opponent: "LAA",
      handedness: "LHP",
      era: 5.14,
      hrPer9: 1.8,
      insight: "Post-surgery velo is down significantly. Angels' RHH core should expose him.",
      tiktokHook: "Boyd's velocity is cooked post-surgery. 1.8 HR per 9 vs righties. The Angels lineup smells blood. #TastyPickEms",
      tier: "Secondary Target"
    },
    {
      name: "Wade Miley",
      team: "CHC",
      opponent: "MIL",
      handedness: "LHP",
      era: 4.98,
      hrPer9: 1.6,
      insight: "42-pitch limit (rehab). By the 3rd inning it's a Cubs bullpen game — 2.3 HR/9 for that pen.",
      tiktokHook: "Wade Miley has a 42-pitch limit today. Anytime HR props ON the Brewers after the 3rd inning. Trust. #TastyPickEms",
      tier: "Secondary Target"
    }
  ],

  // ── 3. TOP HR CANDIDATES (+300 OR LONGER) ───────────────────
  hrCandidates: [
    {
      name: "Aaron Judge",
      team: "NYY",
      position: "RF",
      bats: "R",
      odds: "+340",
      heroStat: "18 HRs vs LHP",
      recentForm: "4 HRs last 7G",
      reasons: [
        "22.1% barrel rate vs southpaws — best in MLB",
        "Boyd allowing 1.8 HR/9, lowest velo of his career",
        "Yankee Stadium right porch + wind out = perfect"
      ],
      tiktokHook: "Judge vs a lefty at The Stadium. Short porch. Wind out. +340 feels like a gift. 🏟️🔥 #TastyPickEms #HRProp",
      tag: "Top Pick",
      featured: true
    },
    {
      name: "Kyle Schwarber",
      team: "PHI",
      position: "LF",
      bats: "L",
      odds: "+380",
      heroStat: "21 HRs on season",
      recentForm: "2 HRs last 5G",
      reasons: [
        ".250 ISO vs righties — elite power vs same-side arms",
        "Opponent starter 2.1 HR/9, highest on their staff",
        "Day game at CBP — his ISO is 70pts higher in day Gs"
      ],
      tiktokHook: "Schwarber's day game ISO vs righties is WILD. +380 and a 1 PM start in Philly. You already know. ☀️ #TastyPickEms",
      tag: "Value"
    },
    {
      name: "Pete Alonso",
      team: "NYM",
      position: "1B",
      bats: "R",
      odds: "+420",
      heroStat: ".280 ISO this month",
      recentForm: "3 HRs last 10G",
      reasons: [
        "90th+ percentile exit velocity — elite raw power",
        "Facing a soft-tosser with 1.6 HR/9 this season",
        "8 HRs in last 20 road games — loves being away"
      ],
      tiktokHook: "The Polar Bear. 90th percentile exit velo. Facing a soft-tosser. +420. Tell me why this isn't a play. 🐻‍❄️ #TastyPickEms",
      tag: "Sneaky"
    },
    {
      name: "Yordan Alvarez",
      team: "HOU",
      position: "DH",
      bats: "L",
      odds: "+310",
      heroStat: "Top 3 barrel rate MLB",
      recentForm: "HR in 2 straight",
      reasons: [
        "Highest hard-hit rate among LHH in MLB (52.3%)",
        "Severino opponents hit .320 ISO vs him this season",
        "Minute Maid amplifies LHH pull power"
      ],
      tiktokHook: "Yordan vs Severino. Best LHH barrel rate in baseball. 5.82 ERA on the mound. +310. This one's obvious. 💪 #TastyPickEms",
      tag: "Top Pick"
    },
    {
      name: "Adolis Garcia",
      team: "TEX",
      position: "RF",
      bats: "R",
      odds: "+450",
      heroStat: "8 HRs last 20G",
      recentForm: ".340 BA last 2 weeks",
      reasons: [
        "On a genuine tear — elevated launch angle all month",
        "+450 price hasn't moved with his recent production",
        "Hot hand equity is real and books are sleeping on it"
      ],
      tiktokHook: "8 HRs in 20 games and the books still have him at +450. They're sleeping. I'm not. 😤 #TastyPickEms #LongShot",
      tag: "Long Shot"
    }
  ],

  // ── 4. SNEAKY VALUE ANGLES ───────────────────────────────────
  sneakyAngles: [
    {
      headline: "Coors Stack — 3 LHH vs RHP",
      detail: "Three Rockies LHH (Bryant, McMahon, Cron) draw a fly-ball RHP with 1.9 HR/9. Coors factor turns everything into a launch pad. Stack all three.",
      odds: "Each +400–+550",
      tag: "Stack Play",
      tiktokHook: "Three LHH in Denver facing a fly-ball RHP. Coors air. Wind out. Stack all three and let it cook. 🏔️ #TastyPickEms #Stack"
    },
    {
      headline: "Bullpen HR — Miley's Pitch Limit",
      detail: "Wade Miley has a 42-pitch limit today on rehab. By inning 3 it's a Cubs pen game — that pen allows 2.3 HR/9. Target any Brewers power bat for anytime HR.",
      odds: "Anytime HR +300–+450",
      tag: "Situational",
      tiktokHook: "Wade Miley has a 42-pitch limit. By the 4th inning the Cubs pen takes over — 2.3 HR per 9. Brewers power bats are live. ⚡ #TastyPickEms"
    },
    {
      headline: "Schwarber Day Game Split",
      detail: "Schwarber hits .267 ISO vs RHP in day games vs .198 in night games this season — a 70-point gap hiding in the splits. Today is a 1:05 PM start at CBP.",
      odds: "HR +380",
      tag: "Split Angle",
      tiktokHook: "The Schwarber day game split is one of the most underrated edges on the board right now. +380. 1 PM start. I'm in. ☀️ #TastyPickEms"
    }
  ],

  // ── 5. SOCIAL / CONTENT ANGLES ──────────────────────────────
  socialAngles: [
    {
      hook: "Judge vs a lefty at The Stadium?",
      caption: "🏟️ AARON JUDGE | NYY\n💰 HR Prop: +340\n🔥 18 HRs vs LHP this season\n\n✅ Best barrel rate vs southpaws in MLB\n✅ Boyd: 1.8 HR/9 — weakest on their staff\n✅ Yankee Stadium short porch + wind out\n\nThis one is automatic. 👀\n#TastyPickEms #MLB #HRProp #Yankees",
      contentIdea: "Reel: slow-motion Judge HR clips with today's matchup overlay and odds badge reveal",
      platform: "TikTok / Reels"
    },
    {
      hook: "Coors + 3 LHH vs RHP = stack city",
      caption: "⛰️ COORS STACK ALERT\n\nThree Rockies LHH bats vs a fly-ball RHP in Denver.\nCoors HR factor: 1.38x. Wind out.\n\n→ Kris Bryant +420\n→ Ryan McMahon +480\n→ C.J. Cron +510\n\nStack all three. Let it cook. 🔥\n#TastyPickEms #MLBBetting #Stack",
      contentIdea: "Carousel: ballpark infographic slide → pitcher card → each hitter breakdown",
      platform: "Instagram Carousel"
    },
    {
      hook: "The Schwarber day split is wild",
      caption: "☀️ KYLE SCHWARBER | PHI\n💰 HR Prop: +380\n\n.267 ISO vs RHP in DAY games\n.198 ISO vs RHP in NIGHT games\n\nThat's a 70-point gap hiding in plain sight.\nIt's 1:05 PM in Philly today.\n\nYou already know. 👀\n#TastyPickEms #Schwarber #Value",
      contentIdea: "Single stat graphic — split comparison bar chart with Schwarber card overlay",
      platform: "Twitter / X"
    },
    {
      hook: "+450 long shot I actually like",
      caption: "😤 ADOLIS GARCIA | TEX\n💰 HR Prop: +450\n\n8 HOME RUNS in his last 20 games.\nBooks haven't adjusted the line.\n\nElevated launch angle all month.\n+450 on a guy this hot is free money talk.\n\nI'm in. 🔥\n#TastyPickEms #LongShot #GarciaTime",
      contentIdea: "30-sec reel: Garcia recent HR montage + scrolling stat graphics + card reveal at end",
      platform: "TikTok / Reels"
    }
  ],

  // ── PROMPT LIBRARY DATA ──────────────────────────────────────
  prompts: [
    {
      player: "Aaron Judge",
      team: "NYY",
      odds: "+340",
      imagePrompt: "Cinematic portrait of a towering MLB power hitter in New York Yankees pinstripes, matte black background, dramatic side lighting with neon green rim light, intense focused expression, bat resting on shoulder, photorealistic, high contrast, sports editorial style",
      captionTemplate: "🏟️ [PLAYER] | [TEAM]\n💰 HR Prop: [ODDS]\n🔥 [HERO STAT]\n\n✅ [REASON 1]\n✅ [REASON 2]\n✅ [REASON 3]\n\n#TastyPickEms #MLB #HRProp #[TEAM]",
      styleNote: "Neon green rim light on dark background. No clutter. Name and odds must be readable at a glance."
    },
    {
      player: "Kyle Schwarber",
      team: "PHI",
      odds: "+380",
      imagePrompt: "Dynamic action shot of a left-handed power hitter in Philadelphia Phillies uniform, mid-swing follow-through, matte black background, neon green energy trails on bat path, cinematic lighting, editorial sports photography style, clean isolated subject",
      captionTemplate: "⚡ [PLAYER] | [TEAM]\n💰 HR Prop: [ODDS]\n📊 [HERO STAT]\n\n[REASON 1]\n[REASON 2]\n[REASON 3]\n\n#TastyPickEms #MLB #ValueBet",
      styleNote: "Action mid-swing. Neon green bat trail effect. High drama."
    },
    {
      player: "Generic Power Hitter",
      team: "[TEAM]",
      odds: "[ODDS]",
      imagePrompt: "Professional baseball player portrait, matte black background, single dramatic spotlight from above, athletic build implied through shadows, minimal but powerful composition, neon green accent glow on jersey details, photorealistic cinematic style",
      captionTemplate: "🎯 [PLAYER] | [TEAM]\n💰 HR Prop: [ODDS]\n📈 [HERO STAT]\n\nHere's the edge:\n1️⃣ [REASON 1]\n2️⃣ [REASON 2]\n3️⃣ [REASON 3]\n\nThis one feels good. 👀\n#TastyPickEms #MLB #PickEms",
      styleNote: "Generic template — fill in team colors in the prompt for variety."
    }
  ]

};
