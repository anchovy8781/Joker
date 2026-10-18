package com.ytx.exchange;

import android.graphics.Color;
import android.os.Bundle;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.WebView;
import androidx.activity.OnBackPressedCallback;
import androidx.core.graphics.Insets;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsCompat;
import androidx.core.view.WindowInsetsControllerCompat;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {

    private static final int APP_BACKGROUND = Color.parseColor("#09090b");

    @Override
    public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        keepContentClearOfSystemBars();
        // Hardware back button walks back through the trading site's pages instead of closing the app.
        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override
            public void handleOnBackPressed() {
                WebView webView = getBridge() != null ? getBridge().getWebView() : null;
                if (webView != null && webView.canGoBack()) {
                    webView.goBack();
                } else {
                    setEnabled(false);
                    getOnBackPressedDispatcher().onBackPressed();
                }
            }
        });
    }

    /**
     * Android 15 (target SDK 35) draws apps edge-to-edge: without this the site's header sits
     * under the status bar, the bottom tab bar under the gesture/navigation bar, and the
     * keyboard covers text inputs. Give the WebView margins equal to the system bars,
     * display cutout and keyboard, and paint the area behind the bars in the app's colour.
     */
    private void keepContentClearOfSystemBars() {
        WebView webView = getBridge() != null ? getBridge().getWebView() : null;
        if (webView == null) return;
        getWindow().getDecorView().setBackgroundColor(APP_BACKGROUND);
        webView.setBackgroundColor(APP_BACKGROUND);
        WindowInsetsControllerCompat bars = WindowCompat.getInsetsController(getWindow(), getWindow().getDecorView());
        bars.setAppearanceLightStatusBars(false);     // light (white) status bar icons on dark
        bars.setAppearanceLightNavigationBars(false);
        ViewCompat.setOnApplyWindowInsetsListener(webView, (View v, WindowInsetsCompat insets) -> {
            Insets sys = insets.getInsets(WindowInsetsCompat.Type.systemBars() | WindowInsetsCompat.Type.displayCutout());
            Insets ime = insets.getInsets(WindowInsetsCompat.Type.ime());
            ViewGroup.MarginLayoutParams lp = (ViewGroup.MarginLayoutParams) v.getLayoutParams();
            lp.leftMargin = sys.left;
            lp.topMargin = sys.top;
            lp.rightMargin = sys.right;
            lp.bottomMargin = Math.max(sys.bottom, ime.bottom);
            v.setLayoutParams(lp);
            return WindowInsetsCompat.CONSUMED;
        });
        ViewCompat.requestApplyInsets(webView);
    }
}
