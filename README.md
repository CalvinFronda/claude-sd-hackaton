# Model Citizens 

> **Making San Diego City Council meetings accessible to every resident.**

[![Live Demo](https://img.shields.io/badge/Live%20Demo-musical--buttercream--df352e.netlify.app-brightgreen)](https://musical-buttercream-df352e.netlify.app/)

---

## The Team

**Model Citizens** — Claude San Diego Hackathon

| Name |  
|------|
| Bijou | 
| Calvin 
| Sarai | 
| Srishti | 
| Rick | 

📁 [GitHub Repository](https://github.com/CalvinFronda/claude-sd-hackaton)

---

## The Problem

Every week, hundreds of San Diego residents take time out of their lives to speak at City Council meetings. They show up. They wait their turn. They say their piece in two minutes or less. And then the minutes get filed away. City council meetings are public — but effectively invisible.

If a San Diego resident wants to know something as simple as:
- *"How much money has the council allocated to homelessness this year?"*
- *"Are housing discussions increasing or declining?"*
- *"What new topics are emerging that weren't discussed 5 years ago?"*

...they'd have to:

1. Navigate the city's clunky Hyland Cloud portal
2. Open individual meeting pages one at a time
3. Read through dense agendas written in bureaucratic language
4. Manually track themes, dollars, and votes across hundreds of meetings
5. Build their own spreadsheets to spot trends

**Everyday San Diegans don't have time for this.** The information is technically public but practically inaccessible.

---

## What It Does

Our dashboard ingests three months of San Diego City Council meeting minutes — every public comment spoken between **December 2025 and March 2026** — strips out the bureaucratic language, finds the patterns, and makes them visible.

- 💬 **Chat Assistant** — Just ask: *"What are people most concerned about?"* or *"What did residents say about housing?"* and get a plain-English answer instantly.
- 📊 **Theme Rankings** — Public comments are automatically grouped into themes, ranked from the issue drawing the most attention down to the least.
- 🎨 **Sentiment Tags** — Each theme is color-coded by sentiment so decision-makers get not just a count of voices, but a read on the room.
- 📣 **Real Quotes** — Surface actual quotes from actual speakers, not summaries.

The result: a single page that turns months of civic participation into one clear, ranked answer — **what are San Diego residents most passionate about right now?**

---

## Who It Helps

| Who | How |
|-----|-----|
| **Residents** | See at a glance what the council is prioritizing |
| **Journalists** | Instantly find voting patterns and emerging topics without weeks of manual research |
| **Advocates & Nonprofits** | Track how often their issue (housing, climate, equity) appears and whether it's trending |
| **Neighborhood Groups** | Search for items affecting their community and see how council voted |
| **Researchers & Students** | Export structured data for academic analysis of local governance trends |
| **Council Members' Offices** | Quick lookup of historical context — *"When did we last discuss water recycling?"* |

---

## Architecture

```
San Diego City Council Meetings (Hyland Cloud Portal)
        │
        ▼
  Weekly Cron Job
  (scrapes agendas + public comment data)
        │
        ▼
     Database
        │
        ├──► Claude NLP
        │    (groups agenda items into plain-language themes,
        │     performs sentiment analysis)
        │
        ▼
  Dashboard Frontend
        │
        ├──► Theme Rankings + Sentiment Visualization
        └──► AI Chat Assistant (natural language Q&A)
```

---

## Data Sources

- [San Diego City Council Agendas & Minutes](https://sandiego.hylandcloud.com/211agendaonlinecouncil) — Hyland Cloud Portal

---

## Core Values

🔍 **Transparency** — Is the city council's time and budget being disproportionately spent on issues less relevant to public concerns?

🗣️ **Accessibility** — City data shouldn't require knowledge of agenda item numbers and bureaucratic naming conventions to navigate.

🤝 **Civic Engagement** — Making it easier for residents to understand and participate in local government.

---

## Live Demo

🌐 [https://musical-buttercream-df352e.netlify.app/](https://musical-buttercream-df352e.netlify.app/)