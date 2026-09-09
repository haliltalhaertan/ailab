from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_batch_page_add_and_clear_without_launching(monkeypatch, tmp_path):
    import lab.batch_lab as batch
    monkeypatch.setattr(batch, 'ROOT', tmp_path)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'pages/6_Paralel_Deneyler.py')).run()
    assert not app.exception
    next(b for b in app.button if b.label == 'Görev listesine ekle').click().run()
    assert not app.exception
    assert len(app.session_state['batch_jobs']) == 1
    next(b for b in app.button if b.label == 'Görev listesini temizle').click().run()
    assert not app.exception
    assert app.session_state['batch_jobs'] == []
