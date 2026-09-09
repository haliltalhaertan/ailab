"""Compact navigation preserving all original routes."""
import streamlit as st
st.set_page_config(page_title='LLM Lab', page_icon=':material/science:', layout='wide')
pages = [
    st.Page('pages/8_Canli_Gorevler.py', title='Çalışmalar', icon=':material/dashboard:', default=True),
    st.Page('pages/10_Yeni_Calisma.py', title='Yeni çalışma', icon=':material/add_circle:'),
    st.Page('pages/9_Arastirma_Bellegi.py', title='Bulgular', icon=':material/menu_book:'),
    st.Page('pages/1_Projeler.py', title='Projeler', icon=':material/folder:'),
    st.Page('pages/7_Directed_Task.py', title='Baş araştırmacı görevi', icon=':material/add_task:'),
    st.Page('pages/6_Paralel_Deneyler.py', title='Yerel hesaplama', icon=':material/calculate:'),
    st.Page('app.py', title='Klasik laboratuvar', icon=':material/biotech:'),
    st.Page('pages/3_Research_Control.py', title='Klasik deney kontrolü', icon=':material/tune:'),
    st.Page('pages/4_Reasoning_Settings.py', title='Model varsayılanları', icon=':material/psychology:'),
    st.Page('pages/5_Code_Experiment_Settings.py', title='Kod çalıştırma', icon=':material/settings:'),
    st.Page('pages/2_Ham_Loglar.py', title='Teknik kayıtlar', icon=':material/description:'),
]
page = st.navigation(pages, position='hidden')
with st.sidebar:
    st.subheader('LLM Lab')
    for entry in pages[:4]:
        st.page_link(entry)
    with st.expander('Ayarlar ve araçlar', expanded=page in pages[7:]):
        for entry in pages[7:]:
            st.page_link(entry)
    if page in pages[4:7]:
        st.caption('Açık çalışma biçimi')
        st.page_link(page)
page.run()
