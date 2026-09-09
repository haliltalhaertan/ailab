"""Choose a workflow without dispatching work."""
import streamlit as st
from pathlib import Path
from lab.task_workbench_ui import render_builder

st.set_page_config(page_title='Yeni çalışma', layout='wide')
st.title('Yeni çalışma')
st.caption('Görevi tanımlayın, taslağı inceleyin ve kuyruğa ekleyin. Çalıştırma ayrı bir adımdır.')
render_builder(Path(__file__).resolve().parents[1])
st.subheader('Diğer çalışma yolları')
for title, description, path, icon in [
    ('Baş araştırmacının görevini çalıştır', 'Tanımlı görev, paralel model kolları ve bütçeli sonuç teslimi.', 'pages/7_Directed_Task.py', ':material/add_task:'),
    ('Yerel hesaplama yap', 'Sonlu matematik kontrolleri veya Python işleri. Model çağrısı yok.', 'pages/6_Paralel_Deneyler.py', ':material/calculate:'),
    ('Araştırma ekibiyle problem araştır', 'Klasik laboratuvar: araştırmacı, eleştirmen ve denetçi ile turlu çalışma.', 'app.py', ':material/biotech:'),
]:
    with st.container(border=True):
        st.page_link(path, label=title, icon=icon)
        st.caption(description)
