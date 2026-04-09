# Bench Test Controller v1.0 — Mimari ve Tasarım Dokümanı

**Hazırlık Tarihi:** Nisan 2026  
**Versiyon:** 1.0  
**Kapsam:** `bench\_test\_controller.py`, `orchestrator.py`, `dropview\_script\_generator.py`, `common.py`

\---

## 1\. Genel Bakış

Bench Test Controller, laboratuvar ortamında elektroanalizör (DropSens / DropView 8400M) ile iki adet seri port kontrollü valfin (SV-01 Multiport ve SY-07B Injector) eş zamanlı yönetimini sağlayan masaüstü bir otomasyon uygulamasıdır.

Kullanıcı; bağlantı kurma, manuel valf kontrolü, ölçüm script'i oluşturma ve sıralı adım bazlı "recipe" çalıştırma işlemlerini tek bir arayüzden gerçekleştirir. Uygulama Windows işletim sistemine özgüdür (win32 API bağımlılığı).

\---

## 2\. Mimari Diyagram

```
┌─────────────────────────────────────────────────────────────┐
│                    MainWindow (PyQt6)                       │
│                                                             │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │ConnectionTab│  │ManualControl │  │ ScriptEditorTab  │   │
│  │             │  │     Tab      │  │                  │   │
│  │ Valve A/B   │  │ Port / State │  │ .scr parametresi │   │
│  │ DropView    │  │ butonları    │  │ XML önizleme     │   │
│  │ bağlantısı  │  │              │  │                  │   │
│  └──────┬──────┘  └──────┬───────┘  └────────┬─────────┘   │
│         │                │                   │             │
│  ┌──────▼──────────────────────────────────── ▼──────────┐  │
│  │                   RecipeTab                           │  │
│  │   RecipeStep listesi │ StepLoop │ RecipeRunner thread │  │
│  └──────────────────────────────────────────────────────┘  │
│                        │                                    │
│              log\_signal (pyqtSignal)                        │
│                        ▼                                    │
│                     LogTab                                  │
└─────────────────────────────────────────────────────────────┘
         │                         │
         ▼                         ▼
┌─────────────────┐     ┌─────────────────────────┐
│ ValveController │     │   DropViewController     │
│ (SV-01, RS-232) │     │   (QObject + QTimer)     │
│                 │     │                          │
│ InjectorValve   │     │  → orchestrator.py       │
│ Controller      │     │    (GUI otomasyon katmanı)│
│ (SY-07B)        │     └────────────┬─────────────┘
└────────┬────────┘                  │
         │                           ▼
         │              ┌────────────────────────┐
         │              │   orchestrator.py       │
         │              │                        │
         │              │  pyautogui + win32      │
         │              │  OpenCV template match  │
         │              │  DropView 8400M GUI     │
         │              │  otomasyon adımları     │
         │              └────────────────────────┘
         │
         ▼
┌─────────────────────┐     ┌───────────────────────────┐
│  SV01Protocol       │     │ dropview\_script\_generator  │
│  (binary frame      │     │ (.scr XML üretici)         │
│   encode/decode)    │     └───────────────────────────┘
└─────────────────────┘
         │
         ▼
  \[COM Port / pyserial]      \[DropView 8400M yazılımı]
  Valve A  |  Valve B         (harici Windows işlemi)
```

\---

## 3\. Teknoloji Yığını

|Katman|Teknoloji|
|-|-|
|GUI|PyQt6 (widget, sinyal/slot, QTimer, QThread)|
|Seri Haberleşme|pyserial|
|GUI Otomasyon|pyautogui, win32api, win32gui, win32clipboard|
|Görüntü İşleme|OpenCV (cv2), PIL/Pillow, numpy|
|Veri Serileştirme|JSON (recipe), XML / xml.etree (script)|
|Derleme Hedefi|PyInstaller (exe)|
|Platform|Windows (zorunlu)|

\---

## 4\. Modül ve Katman Yapısı

### `common.py` — Altyapı Katmanı

Tüm modüllerin ortak bağımlılığıdır. İki ana sorumluluğu vardır: PyInstaller ile exe olarak paketlendiğinde `BASE\_DIR` / `ASSETS\_DIR` yollarını doğru tespit etmek ve `last\_paths.json` üzerinden kullanıcı dosya yolu tercihlerini kalıcı olarak saklamak. PyQt6 dialog yardımcıları (`open\_file`, `save\_file`, `open\_dir`) bu hafıza mekanizmasının üstünde çalışır.

### `dropview\_script\_generator.py` — Script Üretim Katmanı

DropView'ın `.scr` formatını (XML tabanlı) programlı olarak üretir. `generate\_dropview\_script()` fonksiyonu `xml.etree.ElementTree` ile yapıyı oluşturur, `minidom` ile güzel formatlı çıktı yazar. Komut satırından da `prompt\_and\_generate()` ile interaktif kullanım mümkündür. Bu modülün GUI veya orchestrator'a bağımlılığı yoktur; tamamen bağımsız çalışır.

### `orchestrator.py` — GUI Otomasyon Katmanı

DropView 8400M'in harici Windows arayüzünü programlı olarak yönetir. OpenCV `matchTemplate` ile ekran görüntüsü karşılaştırması yaparak düğme ve durum bilgilerini tespit eder; pyautogui ve win32 API'leri ile tıklama, klavye kısayolu ve pencere yönetimi işlemlerini gerçekleştirir. Dört ana aksiyon adımı sunar: `step\_start\_dropview`, `step\_start\_measure`, `step\_stop\_measure`, `step\_exit\_dropview`. Bağlantı durumu `connected\_status.png` / `disconnected\_status.png` şablon görüntüleriyle anlık olarak sorgulanabilir.

### `bench\_test\_controller.py` — Uygulama Katmanı

Uygulamanın giriş noktası ve en kapsamlı modülüdür. Beş ana bileşenden oluşur:

**Protokol ve Donanım Sürücüleri:** `SV01Protocol` SV-01 valfi için binary frame protokolünü (start/end byte, checksum) tanımlar. `ValveController` ve `InjectorValveController` bu protokolü pyserial üzerinden uygulayarak valf işlemlerini thread-safe biçimde yürütür.

**DropViewController:** `QObject`'tan türeyen bu sınıf, orchestrator.py ile GUI arasında köprü görevi görür. Her uzun süren orchestrator çağrısını daemon thread'de çalıştırır; sinyal/slot mekanizmasıyla GUI'yi asla bloke etmez. 3 saniyelik `QTimer` ile DropView bağlantı durumunu arka planda periyodik olarak sorgular.

**RecipeRunner:** `threading.Thread`'i miras alan bu sınıf, kullanıcının tanımladığı adım dizisini sırayla çalıştırır. Valf geçişi, DropView aksiyonu ve süre bekleme mantığını birleştirir. İç içe geçmiş Step Loop desteği sunar. Durum mesajlarını `queue.Queue` üzerinden ana thread'e iletir.

**GUI Sekmeleri:** `ConnectionTab`, `ManualControlTab`, `ScriptEditorTab`, `RecipeTab` ve `LogTab` olmak üzere beş sekmeli bir ana pencere oluşturur.

\---

## 5\. Veri Akışı

### 5.1 Recipe Çalıştırma Akışı

```
Kullanıcı → \[RecipeTab] "Start Recipe"
    │
    ▼
RecipeRunner.start()          ← daemon thread başlar
    │
    ├─ Her adım için:
    │   ├─ DropView aksiyonu (orchestrator çağrısı, senkron)
    │   │       └─ pyautogui + win32 → DropView 8400M GUI
    │   ├─ Valve A → SV01Protocol frame → COM Port
    │   ├─ Valve B → SV01Protocol frame → COM Port
    │   └─ Süre bekleme döngüsü (1sn poll, durdurulabilir)
    │
    ├─ status\_queue.put(("progress", {...}))
    │
    ▼
QTimer (100ms) → RecipeTab.\_poll\_queue()
    │
    └─ GUI güncelleme: progress bar, status label, log
```

### 5.2 DropView Bağlantı Sorgulama Akışı

```
QTimer (3000ms) → DropViewController.\_poll\_connection()
    │
    └─ daemon thread:
        ├─ orchestrator.get\_dropview\_connection\_scores()
        │     ├─ ImageGrab.grab() → ekran görüntüsü
        │     ├─ cv2.matchTemplate(screen, connected.png)
        │     └─ cv2.matchTemplate(screen, disconnected.png)
        └─ pyqtSignal(status\_changed) → GUI güncelleme
```

### 5.3 Script Üretim Akışı

```
Kullanıcı \[ScriptEditorTab] → parametreleri doldurur
    │
    └─ "Kaydet" → dropview\_script\_generator.generate\_dropview\_script()
                    └─ .scr dosyası (XML) → diske yazar
                         └─ RecipeTab step'ine atanır
```

\---

## 6\. Tasarım Kararları

**GUI otomasyonu ile entegrasyon.** DropView 8400M'nin resmi bir API veya SDK'sı bulunmamaktadır. Bu nedenle orchestrator.py, uygulamanın kendi arayüzünü ekran görüntüsü karşılaştırması (OpenCV template matching) ve klavye/fare simülasyonu (pyautogui, win32) aracılığıyla kontrol eder. Bu yaklaşım, DropView kaynak koduna erişim gerektirmeden tam entegrasyon sağlamaktadır.

**Thread mimarisi.** PyQt6 GUI ana thread'de çalışır; uzun süren tüm işlemler (valf haberleşmesi, orchestrator çağrıları, süre bekleme) daemon thread'lerde yürütülür. Bunlar arasındaki iletişim `pyqtSignal` (orchestrator → GUI bildirimleri) ve `queue.Queue` (RecipeRunner → GUI durum güncellemeleri) olmak üzere iki farklı mekanizma ile sağlanır.

**Lazy import stratejisi.** `bench\_test\_controller.py` içinde `orchestrator` modülü doğrudan import edilmemekte; her kullanım noktasında `import orchestrator as orch` şeklinde çağrılmaktadır. Bu, PyInstaller paketlemesini kolaylaştırır ve orchestrator'ın ağır bağımlılıklarının (OpenCV, PIL) uygulama başlangıcını yavaşlatmasını önler.

**DropView exe yolu yönetimi.** Orchestrator, DropView.exe yolunu önce `last\_paths.json`'dan, ardından bilinen kurulum dizinlerinden arar. Hiçbirinde bulamazsa kullanıcıya dosya seçim dialogu açar ve seçimi kalıcı olarak kaydeder. Bu yaklaşım kurulum varsayımını ortadan kaldırmaktadır.

**Recipe doğrulama.** `start\_measure` aksiyonunu içeren bir recipe başlatılmadan önce, DropSens bağlantısının mevcut olup olmadığı ya da recipe içinde daha önce `start\_dropview` adımı bulunup bulunmadığı kontrol edilir. Bu ön doğrulama, çalışma zamanında yarım kalan testlerin önüne geçer.

\---

## 7\. Güçlü Yönler

**Bütünleşik otomasyon.** Hem seri port donanımı hem de harici GUI uygulaması (DropView) tek bir arayüzden yönetilmektedir. Kullanıcının birden fazla uygulama arasında geçiş yapmasına gerek yoktur.

**Esnek recipe motoru.** İç içe Step Loop desteği, recipe-içi döngü (loop\_count) ve adım bazlı DropView aksiyonu atama ile karmaşık test senaryoları JSON formatında kolayca tanımlanıp kaydedilebilir.

**PyInstaller uyumluluğu.** `common.py`'deki `BASE\_DIR` / `ASSETS\_DIR` mantığı, uygulamanın hem geliştirme ortamında hem de derlenmiş exe olarak tutarlı çalışmasını sağlar.

**Non-blocking GUI.** Tüm donanım ve otomasyon çağrıları arka plan thread'lerinde çalıştığından GUI hiçbir zaman donmaz.

\---

## 8\. Zayıf Yönler ve Riskler

**Kırılgan GUI otomasyon katmanı.** Orchestrator.py'nin çalışması, DropView arayüzünün tam olarak beklenen konumda ve görünümde açık olmasına bağlıdır. DropView yazılımının güncellenmesi, tema değişikliği veya ekran ölçekleme farkı (`dpi scaling`) template eşleşmelerini bozabilir ve tespit edilmesi güç hatalara yol açabilir. Şablon görüntüleri (`connected\_status.png` gibi) harici dosyalar olduğundan kaybolmaları veya bozulmaları sessiz hatalara neden olur.

**Windows'a tam bağımlılık.** win32api, pyautogui ve PIL.ImageGrab gibi bileşenler yalnızca Windows'ta çalışır. Kod taşınamazlık açısından kısıtlıdır.

**Thread güvenliği boşlukları.** `DropViewController.\_last\_connected` durumu birden fazla thread tarafından okunup yazılmaktadır; ancak bu erişimler için açık bir kilit (`Lock`) mekanizması bulunmamaktadır. Olası race condition riski düşük olmakla birlikte göz ardı edilmemelidir.

**Geriye dönük uyumluluk kodu.** `orchestrator.py` içindeki `step\_launch\_dropview`, `step\_connect\_dropsens` gibi eski fonksiyonlar ve `bench\_test\_controller.py`'deki `start\_dropview` / `start\_measurement` gibi eski `DropViewController` metodları bakım yükünü artırmaktadır. Zaman içinde bu katman temizlenmelidir.

**Hata kurtarma eksikliği.** Recipe çalışırken bir orchestrator adımı başarısız olduğunda `RecipeRunner` durur ve kullanıcıya hata mesajı gösterir; ancak kaldığı yerden devam etme ya da yeniden deneme mekanizması bulunmamaktadır.

