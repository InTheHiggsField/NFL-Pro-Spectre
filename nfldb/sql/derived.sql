-- Derived tables and views, run in the DuckDB staging file after the raw tables are loaded.
-- Every table and view here is then written to data/nfl.sqlite as a plain table.
-- Money is in millions of US dollars (musd) throughout, as OverTheCap reports it.

------------------------------------------------------------------------------
-- Teams
------------------------------------------------------------------------------
-- Player stats use today's franchise codes (LV, LAC, LA) for every season,
-- while games use the code of the time (OAK, SD, STL). Contracts use nicknames.
-- team_aliases maps all of those to one franchise code.
CREATE OR REPLACE TABLE team_aliases AS
WITH moved(alias, franchise) AS (
    VALUES ('OAK', 'LV'), ('SD', 'LAC'), ('STL', 'LA'), ('LAR', 'LA')
),
base AS (
    SELECT t.team_abbr, t.team_nick, coalesce(m.franchise, t.team_abbr) AS franchise
    FROM teams t LEFT JOIN moved m ON m.alias = t.team_abbr
)
SELECT DISTINCT alias, franchise FROM (
    SELECT team_abbr AS alias, franchise FROM base
    UNION ALL SELECT team_nick, franchise FROM base
    UNION ALL SELECT * FROM (VALUES
        ('Washington', 'WAS'), ('Redskins', 'WAS'), ('Football Team', 'WAS'),
        ('Oilers', 'TEN'), ('JAC', 'JAX'), ('LVR', 'LV'),
        -- Older NFL roster codes (rosters use these for 2002-2015 and before 1999)
        ('ARZ', 'ARI'), ('PHO', 'ARI'), ('BLT', 'BAL'), ('CLV', 'CLE'), ('HST', 'HOU'),
        ('SL', 'LA'), ('RAM', 'LA'), ('RAI', 'LV')) v(alias, franchise)
);

CREATE OR REPLACE TABLE franchises AS
SELECT * FROM teams WHERE team_abbr NOT IN ('OAK', 'SD', 'STL', 'LAR');

------------------------------------------------------------------------------
-- Contracts (OverTheCap via nflverse)
------------------------------------------------------------------------------
CREATE OR REPLACE TABLE contracts AS
SELECT
    row_number() OVER (ORDER BY otc_id, year_signed, team) AS contract_id,
    otc_id, gsis_id, player, position,
    team                                  AS team_raw,      -- "Chiefs", or "NE/IND" for traded deals
    a.franchise                           AS team,          -- franchise code of the signing team
    is_active,
    NULLIF(year_signed, 0)                AS year_signed,
    years,
    value                                 AS value_musd,
    apy                                   AS apy_musd,
    guaranteed                            AS guaranteed_musd,
    apy_cap_pct,
    inflated_value                        AS otc_inflated_value_musd,
    inflated_apy                          AS otc_inflated_apy_musd,
    inflated_guaranteed                   AS otc_inflated_guaranteed_musd,
    height, weight, college, date_of_birth,
    draft_year, draft_round, draft_overall, draft_team,
    player_page
FROM contracts_raw c
LEFT JOIN team_aliases a ON a.alias = split_part(c.team, '/', 1);

-- One row per player per season: cap hit, cash paid, salary breakdown.
-- season_history is the player's whole career and is repeated on each of
-- their contract rows, so it is taken from one row per player.
CREATE OR REPLACE TABLE player_cap_seasons AS
WITH one_per_player AS (
    SELECT otc_id, max(gsis_id) AS gsis_id, any_value(player) AS player,
           any_value(position) AS position, any_value(season_history) AS season_history
    FROM contracts_raw
    GROUP BY otc_id
),
seasons AS (
    SELECT otc_id, gsis_id, player, position, unnest(season_history) AS s FROM one_per_player
)
SELECT
    otc_id, gsis_id, player, position,
    TRY_CAST(s."year" AS INTEGER) AS season,
    NULLIF(s.team, '')            AS team_raw,
    a.franchise                   AS team,
    s.cap_number                  AS cap_hit_musd,
    s.cap_percent                 AS cap_pct,
    s.cash_paid                   AS cash_paid_musd,
    s.base_salary                 AS base_salary_musd,
    s.prorated_bonus              AS prorated_bonus_musd,
    s.roster_bonus                AS roster_bonus_musd,
    s.option_bonus                AS option_bonus_musd,
    s.workout_bonus               AS workout_bonus_musd,
    s.per_game_roster_bonus       AS per_game_roster_bonus_musd,
    s.other_bonus                 AS other_bonus_musd,
    s.guaranteed_salary           AS guaranteed_salary_musd
FROM seasons
LEFT JOIN team_aliases a ON a.alias = NULLIF(s.team, '')
WHERE s."year" <> 'Total' AND TRY_CAST(s."year" AS INTEGER) IS NOT NULL;

------------------------------------------------------------------------------
-- Salary cap and inflation
------------------------------------------------------------------------------
-- Official caps come from seeds/salary_cap.csv. Seasons not in the seed (for
-- example a new season) are estimated from contracts as APY / APY-as-%-of-cap.
CREATE OR REPLACE TABLE salary_cap AS
WITH derived AS (
    SELECT year_signed AS season, round(median(apy_musd / apy_cap_pct), 1) AS cap
    FROM contracts
    WHERE apy_cap_pct >= 0.01 AND year_signed >= 1994
    GROUP BY 1 HAVING count(*) >= 20
),
merged AS (
    SELECT coalesce(s.season, d.season) AS season,
           CASE WHEN s.season IS NOT NULL THEN s.salary_cap_musd ELSE d.cap END AS salary_cap_musd,
           CASE WHEN s.salary_cap_musd IS NOT NULL THEN 'official'
                WHEN s.season IS NOT NULL THEN 'none'
                ELSE 'estimated_from_contracts' END AS source,
           s.note
    FROM salary_cap_seed s FULL OUTER JOIN derived d ON d.season = s.season
),
filled AS (
    -- The uncapped 2010 season gets the midpoint of 2009 and 2011 for adjustments.
    SELECT *, coalesce(salary_cap_musd,
                       (lag(salary_cap_musd) OVER w + lead(salary_cap_musd) OVER w) / 2) AS cap_for_adjustment
    FROM merged WINDOW w AS (ORDER BY season)
)
SELECT season, salary_cap_musd, source, note, cap_for_adjustment,
       (SELECT cap_for_adjustment FROM filled ORDER BY season DESC LIMIT 1) / cap_for_adjustment AS cap_factor_to_latest
FROM filled
ORDER BY season;

-- CPI factor to the latest full year of CPI data (empty if FRED was unreachable).
CREATE OR REPLACE VIEW v_cpi_factors AS
SELECT year, cpi_u,
       (SELECT cpi_u FROM cpi_annual WHERE months = 12 ORDER BY year DESC LIMIT 1) / cpi_u AS cpi_factor_to_latest
FROM cpi_annual;

------------------------------------------------------------------------------
-- Views for the dashboard, value portal and model
------------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_player_cap_by_season AS
SELECT gsis_id, season,
       sum(cap_hit_musd)   AS cap_hit_musd,
       sum(cash_paid_musd) AS cash_paid_musd,
       sum(cap_pct)        AS cap_pct
FROM player_cap_seasons
WHERE gsis_id IS NOT NULL AND season IS NOT NULL
GROUP BY ALL;

-- One row per player per season (REG and POST rows; filter season_type).
-- Adds bio, age on Sept 1, draft info, and that season's cap hit in both
-- raw dollars and today's cap dollars.
CREATE OR REPLACE VIEW v_player_season AS
SELECT
    s.*,
    p.birth_date, p.height, p.weight, p.college_name,
    p.rookie_season, p.draft_year, p.draft_round, p.draft_pick, p.draft_team,
    round(date_diff('day', TRY_CAST(p.birth_date AS DATE), make_date(s.season, 9, 1)) / 365.25, 1) AS age,
    s.season - p.rookie_season + 1          AS career_year,
    c.cap_hit_musd, c.cash_paid_musd, c.cap_pct,
    sc.salary_cap_musd,
    c.cap_hit_musd * sc.cap_factor_to_latest AS cap_hit_latest_cap_musd
FROM player_stats_season s
LEFT JOIN players p              ON p.gsis_id = s.player_id
LEFT JOIN v_player_cap_by_season c ON c.gsis_id = s.player_id AND c.season = s.season
LEFT JOIN salary_cap sc          ON sc.season = s.season;

-- Contracts with two kinds of inflation adjustment:
--   *_cap_adj: scaled by salary cap growth (how the NFL market actually moves)
--   *_cpi_adj: scaled by consumer prices (general inflation)
CREATE OR REPLACE VIEW v_contracts AS
SELECT
    c.*,
    sc.salary_cap_musd                         AS cap_in_year_signed_musd,
    c.apy_musd        * sc.cap_factor_to_latest AS apy_cap_adj_musd,
    c.value_musd      * sc.cap_factor_to_latest AS value_cap_adj_musd,
    c.guaranteed_musd * sc.cap_factor_to_latest AS guaranteed_cap_adj_musd,
    c.apy_musd        * cpi.cpi_factor_to_latest AS apy_cpi_adj_musd,
    c.value_musd      * cpi.cpi_factor_to_latest AS value_cpi_adj_musd
FROM contracts c
LEFT JOIN salary_cap sc   ON sc.season = c.year_signed
LEFT JOIN v_cpi_factors cpi ON cpi.year = c.year_signed;

-- Win/loss record per franchise per season, from game results.
CREATE OR REPLACE VIEW v_team_record AS
WITH g AS (
    SELECT season, game_type, home_team AS team, home_score AS pf, away_score AS pa
    FROM games WHERE home_score IS NOT NULL
    UNION ALL
    SELECT season, game_type, away_team, away_score, home_score
    FROM games WHERE away_score IS NOT NULL
)
SELECT
    g.season,
    coalesce(a.franchise, g.team)                               AS team,
    max(g.team)                                                 AS team_code_that_season,
    count(*) FILTER (WHERE game_type = 'REG')                   AS games,
    count(*) FILTER (WHERE game_type = 'REG' AND pf > pa)       AS wins,
    count(*) FILTER (WHERE game_type = 'REG' AND pf < pa)       AS losses,
    count(*) FILTER (WHERE game_type = 'REG' AND pf = pa)       AS ties,
    sum(pf)  FILTER (WHERE game_type = 'REG')                   AS points_for,
    sum(pa)  FILTER (WHERE game_type = 'REG')                   AS points_against,
    sum(pf - pa) FILTER (WHERE game_type = 'REG')               AS point_diff,
    count(*) FILTER (WHERE game_type <> 'REG') > 0              AS made_playoffs,
    count(*) FILTER (WHERE game_type <> 'REG' AND pf > pa)      AS playoff_wins,
    coalesce(bool_or(game_type = 'SB' AND pf > pa), false)      AS won_super_bowl
FROM g LEFT JOIN team_aliases a ON a.alias = g.team
GROUP BY 1, 2;

-- Team season: record + regular season team stat totals + team info.
CREATE OR REPLACE VIEW v_team_season AS
SELECT r.*, f.team_name, f.team_conf, f.team_division, f.team_color, f.team_logo_espn,
       ts.* EXCLUDE (season, team, season_type)
FROM v_team_record r
LEFT JOIN franchises f ON f.team_abbr = r.team
LEFT JOIN team_stats_season ts
       ON ts.season = r.season AND ts.team = r.team AND ts.season_type = 'REG';

-- Team payroll by season (sum of cap hits of players OverTheCap lists for that team).
CREATE OR REPLACE VIEW v_team_payroll AS
SELECT season, team, count(*) AS players, sum(cap_hit_musd) AS cap_hit_musd,
       sum(cash_paid_musd) AS cash_paid_musd
FROM player_cap_seasons
WHERE team IS NOT NULL AND season IS NOT NULL
GROUP BY 1, 2;
