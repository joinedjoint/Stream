import streamlit as st
import pandas as pd
import gspread
import google.generativeai as genai
import plotly.express as px
from datetime import datetime, timedelta
import json
import os
import io

# --- CONFIGURATION & SETUP ---
st.set_page_config(
    page_title="OS4 Hockey League Hub",
    page_icon="🏒",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Configure Gemini AI
try:
    # We still read the simple API key from secrets.toml
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
except Exception:
    st.error("Gemini API Key missing in secrets.toml. AI Recaps will not work.")

# --- STYLING (CSS) ---
st.markdown("""
    <style>
    .stApp {
        background-color: #0e1117;
        color: #fafafa;
    }
    [data-testid="stSidebar"] {
        background-color: #161b22;
        border-right: 1px solid #30363d;
    }
    h1, h2, h3 {
        color: #58a6ff;
        font-family: 'Helvetica Neue', sans-serif;
        font-weight: 800;
        text-transform: uppercase;
    }
    .stat-card {
        background-color: #21262d;
        padding: 15px;
        border-radius: 10px;
        border: 1px solid #30363d;
        text-align: center;
    }
    .big-number {
        font-size: 2rem;
        font-weight: bold;
        color: #58a6ff;
    }
    /* DataFrame styling */
    .stDataFrame {
        border: 1px solid #30363d;
    }
    </style>
""", unsafe_allow_html=True)

# ----------------------------------------------------------------------
# --- DATA LOADING (ULTRA-ROBUST FIX) ---
# ----------------------------------------------------------------------
@st.cache_data(ttl=600) 
def load_data():
    team_map = {}
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
             config_data = json.load(f)
             team_map = config_data.get('team_ids', {})
    except (FileNotFoundError, json.JSONDecodeError):
        st.warning("Could not load config.json for team names. Team names will show as IDs.")

    try:
        # Authentication block simplified for reliability (assuming external fix or working credentials)
        if not os.path.exists('credentials.json'):
            st.error("FATAL: credentials.json not found. Place it in the app.py folder.")
            return pd.DataFrame(), pd.DataFrame(), {}
            
        gc = gspread.service_account(filename='credentials.json')
        
        sh = gc.open("OS4 League Game Stats") 
        ws_stats = sh.worksheet("Player Stats")

        all_data = ws_stats.get_all_values()
        
        if not all_data or len(all_data) < 2:
            st.error("Sheet is empty or only contains headers. Cannot load data.")
            return pd.DataFrame(), pd.DataFrame(), {}

        # 1. Clean Headers: Strip whitespace, force lowercase.
        raw_headers = [h.strip().lower() for h in all_data[0]]
        
        # 2. DEFENSIVE FIX: Force column names by index if header is blank
        if len(raw_headers) > 6 and raw_headers[6] == '':
             raw_headers[6] = 'position' 
        if len(raw_headers) > 5 and raw_headers[5] == '':
             raw_headers[5] = 'team id' 
             
        data_rows = all_data[1:]
        
        df = pd.DataFrame(data_rows, columns=raw_headers)
        
        # --- RENAME COLUMNS ---
        column_rename_map = {
             'team id': 'club id',           
             'offense rating': 'rating offense',
             'defense rating': 'rating defense',
             'teamplay rating': 'rating teamplay',
             'toi': 'toi (secs)',            
        }
        df.rename(columns=column_rename_map, inplace=True) 
        
        # Verify essential columns existence after renaming
        essential_cols = ['position', 'club id', 'date', 'goals against']
        missing_cols = [col for col in essential_cols if col not in df.columns]
        
        if missing_cols:
            st.error(f"FATAL ERROR: Essential column(s) {', '.join(missing_cols)} still missing after header cleanup. Check your sheet's first row (A1:AZ1).")
            st.stop()

        # Load Standings (Standard Sheet)
        ws_standings = sh.worksheet("Standings")
        df_standings = pd.DataFrame(ws_standings.get_all_records())
        
        # Convert numeric columns safely
        numeric_cols = ['goals', 'assists', 'points', 'shots', 'saves', 'goals against', 'toi (secs)', 'pims', 'hits', 'blocked shots']
        for col in numeric_cols:
            if col in df.columns:
                df[col] = df[col].astype(str).str.replace(r'[^\d.]', '', regex=True)
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).astype(int)

        # Ensure Points is calculated 
        if 'points' not in df.columns or df['points'].sum() == 0:
             df['points'] = df['goals'] + df['assists']
        
        # Convert Date 
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'], unit='s', errors='coerce')
        
        # Map Club ID (renamed from Team ID) to Team Name
        df['team name'] = df['club id'].astype(str).map(team_map).fillna("Unknown Team ID: " + df['club id'].astype(str))
        
        return df, df_standings, team_map

    except Exception as e:
        st.error(f"Error loading data: {e}")
        return pd.DataFrame(), pd.DataFrame(), {}

df_full, df_standings, team_map = load_data()

# ----------------------------------------------------------------------
# --- SIDEBAR FILTERS AND PAGES ---
# ----------------------------------------------------------------------
st.sidebar.image("https://i.imgur.com/s4a2Y4c.png", width=100) 
st.sidebar.title("OS4 HL Menu")

page = st.sidebar.radio("Navigate", ["Standings", "Home & Recaps", "League Leaders", "Player Stats", "Goalie Stats", "Team Rosters"])

st.sidebar.markdown("---")
st.sidebar.subheader("📅 Timeframe Filter")

if not df_full.empty and 'date' in df_full.columns:
    df_valid_dates = df_full.dropna(subset=['date'])
    
    if not df_valid_dates.empty:
        min_date = df_valid_dates['date'].min().date()
        max_date = df_valid_dates['date'].max().date()
        
        filter_type = st.sidebar.selectbox("View By:", ["Season (All Time)", "Last 7 Days", "Specific Range"])
        
        if filter_type == "Last 7 Days":
            start_date = max_date - timedelta(days=7)
            end_date = max_date
        elif filter_type == "Specific Range":
            start_date = st.sidebar.date_input("Start Date", min_date)
            end_date = st.sidebar.date_input("End Date", max_date)
        else:
            start_date = min_date
            end_date = max_date

        # Filter using lowercase 'date' column
        df = df_full[(df_full['date'].dt.date >= start_date) & (df_full['date'].dt.date <= end_date)].copy()
    else:
        df = df_full.copy()
        st.info("No valid date/timestamp data found to filter by.")
else:
    df = df_full.copy()
    if df_full.empty:
        st.error("No data loaded from Google Sheet.")


def generate_recap(match_id):
    if not st.secrets.get("GEMINI_API_KEY"):
         return "AI Recap not configured. Please add GEMINI_API_KEY to secrets.toml."
         
    # All column access is now lowercase
    match_data = df_full[df_full['match id'] == match_id]
    if match_data.empty:
        return "No data found."
    
    stats_summary = []
    
    for team_id in match_data['club id'].unique():
        team_name = team_map.get(str(team_id), f"Team {team_id}")
        team_rows = match_data[match_data['club id'] == team_id]
        
        team_score = team_rows['goals'].sum()
        
        if not team_rows.empty:
            top_player = team_rows.loc[team_rows['points'].idxmax()]
            stats_summary.append(f"**{team_name}**: Scored {team_score} goals. Top scorer: {top_player['username']} ({top_player['goals']}G, {top_player['assists']}A).")
        else:
            stats_summary.append(f"**{team_name}**: Scored {team_score} goals. (Player stats unavailable).")
    
    prompt = f"""
    You are an energetic hockey sportscaster for the OS4 League. Write a 4-sentence, punchy recap of this game. 
    Highlight the final score, mention the best-performing player, and use hockey slang. 
    
    Game Summary:
    {'\n'.join(stats_summary)}
    """
    
    try:
        model = genai.GenerativeModel('gemini-pro')
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Error generating AI recap: {e}"

if page == "Standings":
    st.title("🥇 OS4 League Standings")
    st.markdown("Current league standings from the Google Sheet.")
    
    if not df_standings.empty:
        # Note: df_standings still uses original sheet headers like 'Team Name'
        st.dataframe(
            df_standings[['Team Name', 'GP', 'W', 'L', 'OTL', 'PTS']], 
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("Standings data is not yet available.")


elif page == "Home & Recaps":
    st.title("🏟️ OS4 League Center")
    st.markdown("Welcome to the official stats hub of the OS4 Hockey League.")
    
    st.subheader("📢 Recent Game Recaps (AI Powered)")
    
    if not df.empty:
        recent_matches = df.sort_values('date', ascending=False)['match id'].unique()[:5]
        
        for match_id in recent_matches:
            match_rows = df[df['match id'] == match_id]
            date_str = match_rows['date'].iloc[0].strftime("%B %d, %Y")
            
            with st.expander(f"Match ID: {match_id} - {date_str}", expanded=True):
                col1, col2 = st.columns([3, 1])
                with col1:
                    if st.button(f"Generate Recap for {match_id}", key=match_id):
                        with st.spinner("AI Sportscaster is typing..."):
                            recap = generate_recap(match_id)
                            st.success(recap)
                    
                    summary = match_rows.groupby('team name')[['goals']].sum().reset_index()
                    summary.columns = ['Team Name', 'Goals'] 
                    st.dataframe(summary, hide_index=True)

    else:
        st.info("No game data found for the selected timeframe.")

elif page == "League Leaders":
    st.title("🏆 League Leaders")
    
    if df.empty:
        st.info("No data available to generate leaders.")
        st.stop()

    agg_stats = df.groupby('username').agg({
        'points': 'sum',
        'goals': 'sum',
        'assists': 'sum',
        'shots': 'sum',
        'hits': 'sum',
        'pims': 'sum'
    }).reset_index().sort_values('points', ascending=False)
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.markdown("### 🥅 Points")
        top_pts = agg_stats.sort_values('points', ascending=False).head(10)[['username', 'points']]
        top_pts.columns = ['Username', 'Points'] 
        st.table(top_pts)
        
    with col2:
        st.markdown("### 🎯 Goals")
        top_goals = agg_stats.sort_values('goals', ascending=False).head(10)[['username', 'goals']]
        top_goals.columns = ['Username', 'Goals'] 
        st.table(top_goals)

    with col3:
        st.markdown("### 🧱 Hits")
        top_hits = agg_stats.sort_values('hits', ascending=False).head(10)[['username', 'hits']]
        top_hits.columns = ['Username', 'Hits'] 
        st.table(top_hits)

    st.subheader("Top 10 Points Distribution")
    fig = px.bar(agg_stats.head(10), x='username', y='points', color='points', color_continuous_scale='Bluered')
    fig.update_layout(paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', font_color='white')
    st.plotly_chart(fig, use_container_width=True)


elif page == "Player Stats":
    st.title("🏒 Skater Statistics")
    
    skater_df = df[df['position'] != 'goalie']
    
    grouped_skaters = skater_df.groupby(['username', 'team name']).agg(
        GP=('match id', 'nunique'),
        Goals=('goals', 'sum'),
        Assists=('assists', 'sum'),
        Points=('points', 'sum'),
        Shots=('shots', 'sum'),
        Hits=('hits', 'sum'),
        PIMs=('pims', 'sum')
    ).reset_index()
    
    grouped_skaters.columns = ['Username', 'Team Name', 'GP', 'Goals', 'Assists', 'Points', 'Shots', 'Hits', 'PIMs']
    
    st.dataframe(
        grouped_skaters.sort_values('Points', ascending=False),
        use_container_width=True,
        hide_index=True
    )

elif page == "Goalie Stats":
    st.title("🛡️ Goalie Statistics")
    
    goalie_df = df[df['position'] == 'goalie']
    
    if not goalie_df.empty:
        grouped_goalies = goalie_df.groupby(['username', 'team name']).agg(
            GP=('match id', 'nunique'),
            Saves=('saves', 'sum'),
            GA=('goals against', 'sum'),
            TOI_secs=('toi (secs)', 'sum')
        ).reset_index()
        
        grouped_goalies['SV%'] = grouped_goalies['Saves'] / (grouped_goalies['Saves'] + grouped_goalies['GA'])
        
        grouped_goalies['GAA'] = (grouped_goalies['GA'] * 3600) / grouped_goalies['TOI_secs'].replace(0, 1) 
        
        grouped_goalies['SV%'] = grouped_goalies['SV%'].map('{:.3f}'.format)
        grouped_goalies['GAA'] = grouped_goalies['GAA'].map('{:.2f}'.format)
        
        grouped_goalies.columns = ['Username', 'Team Name', 'GP', 'Saves', 'GA', 'TOI_secs', 'SV%', 'GAA']

        st.dataframe(
            grouped_goalies[['Username', 'Team Name', 'GP', 'Saves', 'GA', 'SV%', 'GAA']].sort_values('GP', ascending=False), 
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("No goalie stats recorded yet.")

elif page == "Team Rosters":
    st.title("👥 Team Rosters & Stats")
    
    teams = df['team name'].unique()
    
    selected_team = st.selectbox("Select a Team", teams)
    
    team_data = df[df['team name'] == selected_team]
    
    total_goals = team_data['goals'].sum()
    total_games = team_data['match id'].nunique()
    
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Games Played", total_games)
    with col2:
        st.metric("Total Goals Scored", total_goals)
    
    st.subheader("Roster Breakdown")
    
    roster = team_data.groupby(['username', 'position']).agg(
        GP=('match id', 'nunique'),
        Points=('points', 'sum'),
        Goals=('goals', 'sum'),
        Assists=('assists', 'sum')
    ).reset_index()
    
    roster.columns = ['Username', 'Position', 'GP', 'Points', 'Goals', 'Assists']
    
    st.dataframe(roster.sort_values('Points', ascending=False), use_container_width=True, hide_index=True)

# Footer
st.markdown("---")
st.markdown("OS4 Hockey League Stats Hub | Powered by Streamlit, Google Sheets, & Gemini AI")
