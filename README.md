# Variance Explanation Assistant

A tool that helps finance analysts quickly understand and explain the 
gap between forecasted and actual numbers — without manually digging 
through spreadsheets every month-end.

## Problem
When leadership asks "why did we miss the forecast?", analysts often 
spend hours manually comparing numbers to figure out the cause. This 
tool automates that first pass of analysis.

## How It Works
1. User enters forecast vs actual data (by category) in a structured form
2. A logic layer (plain code, no AI) calculates variance %, ranks the 
   biggest drivers, and applies rule-based pre-classification 
   (price / timing / volume / anomaly)
3. Only this pre-computed data is sent to the AI, which generates a 
   clear, one-sentence explanation — never inventing numbers on its own
4. Results are shown with a confidence score, and low-confidence 
   explanations are flagged for manual review
5. Every report is saved to a database and viewable in History

## Tech Stack
- Backend: Flask (Python)
- Database: SQLite
- AI: OpenAI API (gpt-4o-mini)
- Frontend: HTML/CSS

## Development Process & Commit History

This project was built incrementally, with commits mapped to each stage 
of the required pipeline:

1. **Initial Setup** — Project scaffolding and product brief
2. **Structured Input Form** — Manual entry grid for forecast/actual data (no chatbox UI)
3. **Logic Layer** — Variance calculation, ranking, and rule-based 
   pre-classification (runs before any AI call)
4. **AI Integration** — OpenAI API call for generating variance explanations, 
   using only pre-computed data to prevent hallucinated causes
5. **Database Persistence** — SQLite storage for all submitted reports
6. **Admin Dashboard / History** — View of past analyses and confidence trends

Full commit history is available in this repository, showing iterative 
development and refinement across each stage.

## Key Design Principle: Logic Before AI
The AI is only used to narrate explanations in plain English — all the 
actual math (variance %, ranking, classification) happens in code first. 
This prevents the AI from inventing causes that aren't supported by the 
data, which was identified as the key failure mode for this type of tool.
