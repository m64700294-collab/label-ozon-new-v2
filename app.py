FONT_NAME = "RobotoFull"
FONT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "Roboto_Full_Final.ttf"
)

def register_font():
    if not os.path.exists(FONT_PATH):
        return False

    try:
        pdfmetrics.registerFont(
            TTFont(
                FONT_NAME,
                FONT_PATH
            )
        )
        return True
    except Exception as e:
        st.error(
            f"Ошибка загрузки шрифта: {e}"
        )
        return False


FONT_OK = register_font()
