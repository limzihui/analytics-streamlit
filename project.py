import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
import yfinance as yf
import time

#portfolio optimisation module
from pypfopt.efficient_frontier import EfficientFrontier
from pypfopt import risk_models
from pypfopt import expected_returns
from pypfopt.cla import CLA
from matplotlib.ticker import FuncFormatter
from pypfopt import discrete_allocation
import pypfopt.plotting as pplt

#import from other files
from helpers import filedownload

def set_page_config():
    st.set_page_config(layout="wide")
    st.title('ESG Portfolio Optimiser (S&P)')
    
    st.markdown("""
    This app retrieves the list of the **S&P 500** (from Wikipedia) and its corresponding **stock closing price** (year-to-date)! 
    This app will allow you to remove industry codes from the universe to generate your ESG portfolio 
    * **Python libraries:** base64, pandas, streamlit, numpy, matplotlib, seaborn, pypfopt, yfinance
    * **Data source:** 
        * [Wikipedia](https://en.wikipedia.org/wiki/List_of_S%26P_500_companies).
        * [yfinance](https://github.com/ranaroussi/yfinance).
    """)

def set_sidebar(combined_df):
    # returns all user inputs from side bar
    st.sidebar.header('User Input Features')

    # Sidebar - Sector selection
    sorted_sector_unique = sorted( combined_df['GICS Sub-Industry'].unique() )
    selected_sector = st.sidebar.multiselect('Sector to remove', sorted_sector_unique, ['Tobacco','Casinos & Gaming','Aerospace & Defense'])

    #Parameter: maximum weight of 1 asset
    max_wt = st.sidebar.slider('Max weight (%)', 0, 100, value=10)/100

    #Parameter: minimum weight for 1 asset
    min_wt = st.sidebar.slider('Min weight (%)', -100, 0, value=0)/100

    #Parameter: minimum esg score
    min_esg_score = st.sidebar.slider('Minimum ESG score (0-100)', 1, 100, value=80)
    
    #Parameter: objective function
    objective_fn = st.sidebar.selectbox("Objective Function", ['Max Sharpe', 'Min Vol'], help="Use the Critical Line Algorithm to solve for selected objective function")

    #Parameter: Risk free rate
    risk_free_rate = st.sidebar.number_input("Risk Free Rate (%)", min_value = 0.0, max_value=20.0, step=0.01, value=6.5)/100


    return selected_sector, min_wt, max_wt, min_esg_score, objective_fn, risk_free_rate
    
@st.cache
def load_snp_data():
    # Web scraping of S&P 500 data
    url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
    html = pd.read_html(url, header = 0)
    df = html[0]
    return df

@st.cache
def load_esg_scores():
    esg_scores = pd.read_excel(ESG_SCORE_FILENAME)
    return esg_scores

def load_all_data():
    snp_data = load_snp_data()
    esg_scores = load_esg_scores()
    return pd.merge(snp_data,esg_scores,on='Symbol').sort_values(by="Symbol").reset_index(drop=True)

def clean_data(combined_df, selected_sector, min_esg_score):
    # Filtering data
    combined_df_filtered = combined_df[ ~combined_df['GICS Sub-Industry'].isin(selected_sector) ]
    combined_df_filtered = combined_df_filtered.drop(columns=['SEC filings','CIK','ticker_name'])
    
    # Removing tickers below ESG threshold set
    #combined_df = combined_df[combined_df['esg_score']>min_esg_score] OLD
    combined_df_filtered = combined_df_filtered[combined_df_filtered['esg_score']>min_esg_score].dropna(axis=1,how='all') #NEW
    
    # Reset index for cleaner display
    combined_df_filtered = combined_df_filtered.reset_index(drop=True)
    #return combined_df OLD
    return combined_df_filtered #NEW

def display_filtered_universe(combined_df_filtered):
    esg_positive_tickers = combined_df_filtered.Symbol
    st.header('Universe')
    st.write('Data Dimension: ' + str(combined_df_filtered.shape[0]) + ' rows and ' + str(combined_df_filtered.shape[1]) + ' columns.')
    st.dataframe(combined_df_filtered)
    st.markdown(filedownload(combined_df_filtered, "SP500.csv","Download ticker universe as CSV"), unsafe_allow_html=True)

# note cache causes some error if code is not ready
@st.cache 
def load_price_data(combined_df_filtered):
    data = yf.download(
            tickers = list(combined_df_filtered.Symbol),
            period = "ytd",
            interval = "1D",
            threads = True
        )
        #drop tickers that can't find data
    cleaned_adj_close = data['Adj Close'].dropna(axis=1,how='all')
    return cleaned_adj_close

def run_ef_model(cleaned_adj_close, weight_bounds, objective_fn, risk_free_rate):
    min_wt, max_wt = weight_bounds
    #Annualised return
    mu = expected_returns.mean_historical_return(cleaned_adj_close)
    #Sample var
    Sigma = risk_models.sample_cov(cleaned_adj_close)
    ef = CLA(mu, Sigma, weight_bounds=(min_wt,max_wt))
    
    fig, ax = plt.subplots()
    ax = pplt.plot_efficient_frontier(ef, ef_param="risk", show_assets=True)
    if objective_fn == "Max Sharpe":
        asset_weights = ef.max_sharpe()
    elif objective_fn == "Min Vol":
        asset_weights = ef.min_volatility()
    ret_tangent, std_tangent, _ = ef.portfolio_performance(risk_free_rate = risk_free_rate)
    
    ax.scatter(std_tangent, ret_tangent, marker="*", s=100, c="r", label=objective_fn)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: '{:.0%}'.format(x)))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda y, _: '{:.0%}'.format(y)))
    ax.legend()
    clean_wts = {key:value for (key,value) in asset_weights.items() if value != 0}

    # Separate into 2 columns 
    col1, col2 = st.columns(2)
    col1.markdown(f"### Efficient Frontier: {objective_fn}")
    col1.pyplot(fig)

    col2.markdown("### Optimal portfolio weights:")
    fig2, ax2 = plt.subplots()
    ax2 = pplt.plot_weights(clean_wts)
    col2.pyplot(fig2)
    col2.write(clean_wts)
    col2.markdown(f'Annualised Returns: {ret_tangent*100:.2f}%  \n Sigma: {std_tangent*100:.2f}%  \n Sharpe Ratio: {(ret_tangent-risk_free_rate)/std_tangent:.2f}')
    
    # For performance plotting
    return asset_weights

# wrote a simple function for performance plotting for now; have not included it in the main() body yet
def plot_portfolio_performance(cleaned_adj_close, asset_weights, benchmark):
    returns = np.log(cleaned_adj_close).diff()
    for asset, weight in asset_weights.items():
        returns[asset] = returns[asset] * weight
    returns['Portfolio_Ret'] = returns.sum(axis=1, skipna=True)
    returns = returns.cumsum()
    
    benchmark_adj_close = yf.download(
                            tickers = benchmark,
                            period = "ytd",
                            interval = "1D",
                            threads = True
                        )['Adj Close'].rename(benchmark)
    benchmark_returns = np.log(benchmark_adj_close).diff().cumsum()
    benchmark_returns.iloc[0] = 0
    
    returns = returns.join(benchmark_returns)
    
    fig, ax = plt.subplots()
    ax = plt.plot(returns[['Portfolio_Ret', "^GSPC"]])
        
        
# Global variables
ESG_SCORE_FILENAME = "esg_scores.xlsx"

def main():
    # Main logic
    set_page_config()
    combined_df = load_all_data()
    selected_sector, min_wt, max_wt, min_esg_score, objective_fn, risk_free_rate = set_sidebar(combined_df)
    combined_df_filtered = clean_data(combined_df, selected_sector, min_esg_score)
    display_filtered_universe(combined_df_filtered)

    if st.button(f'Load Price data for {len(combined_df_filtered)} tickers'):
        cleaned_adj_close = load_price_data(combined_df_filtered)
        st.markdown(filedownload(cleaned_adj_close, "adj_close.csv","Download price data as CSV", index=True), unsafe_allow_html=True)
    else:
        st.stop()

    asset_weights = run_ef_model(cleaned_adj_close, weight_bounds=(min_wt,max_wt), objective_fn = objective_fn, risk_free_rate=risk_free_rate)

    #plot price of portfolio over last few days
    
main()


