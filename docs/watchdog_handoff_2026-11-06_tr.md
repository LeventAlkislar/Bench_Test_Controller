# Discrete PAD watchdog devir notu — 6 Kasım 2026

## Amaç ve kapsam

Bench Test Controller (BTC), uzun süreli Discrete PAD deneyinde DropView, Multiscript veya DropSens kontrolü kaybolduğunda mümkünse deneyi gözetimsiz sürdürebilmeli. İlk kapsam Discrete PAD. Continuous PAD ve CV bu değişikliğin dışında. Kurtarma için Windows yeniden başlatma, güç kesme ve Arduino pompa ayarı değişikliği planlanmadı. Saha testlerinde BTC simülasyon modundaydı, DropView ve DropSens gerçek ölçüm yaptı. Canlı seri Flexicon bağlantısı yoktu.

## Son onaylanan davranış

| Durum | BTC’nin hedef davranışı |
| --- | --- |
| Yalnızca Multiscript penceresi kayboldu | DropView işlemi ve DropSens bağlantısı sürüyorsa editörü aynı oturumda yeniden açmayı dene. Sırf pencere kapandı diye **Stop, Load, Run veya abort** uygulama. Eski scriptin çalıştığı ve beklenen CSV akışının sürdüğü doğrulanırsa mevcut ölçüm, adım sayacı ve paket devam eder. Adım bittiğinde sıradaki komut DropView kontrolü gerektiriyorsa, kontrol doğrulanana kadar o komutu gönderme. Kontrol geri gelmezse doğrulanmış kontrol kaybı akışına geç. |
| Ölçüm gerçekten durmuş, DropView ve bağlantı açık | Taze okumalarla duruşu doğrula. Reçetenin kontrol edilmeden sonraki komuta geçmesini engelle. Eski ölçümün durumu ve cihaz kontrolü doğrulanabiliyorsa kayıtlı reçete konumundan kurtar. |
| DropView işlemi gerçekten sonlanmış | Önce izlenen eski işlemin sonlandığını doğrula. DropView’u aç, DropSens’e bağlan ve kontrolü doğrula. Kullanıcı kendi kurulumunda çökme sonrası ölçümün durduğunu DropSens LED’lerinden gözlemledi. BTC LED okuyamıyor. Eski ölçümün durduğu yönündeki otomatik kararın hangi bağlantı yolu ve arıza biçimi için geçerli olduğu saha doğrulamasına bağlı. Yeni oturumda varsayılan script görünmesi tek başına bu kanıtı sağlamaz. Devam akışı yeni ölçüm gerektirirse scripti yeniden yükle ve yeni pakette başlat. |
| DropView penceresi görünmüyor ya da yanıt belirsiz | Örtülü ve küçültülmüş pencereyi gerçek işlem çıkışından ayır. Ekran okuması öncesi hedef pencereyi öne getir. Şüpheyi taze örneklerle doğrula ve kontrolü geri kazanmaya çalış. Yalnızca pencere kaybını eski ölçümün durduğuna kanıt sayma. |
| DropSens gerçekten disconnected | Bağlantı kaybını doğrula. Windows’ta bulunmayan COM portunu tekrar tekrar deneme. Mevcut portları tara ve gerektiğinde USB otomatik bağlantı yoluna geç. Son başarılı yol kaydediliyor. Bağlantı geri geldiğinde eski ölçümün durumunu ve kontrolü ayrıca doğrula. |
| DropView’da “Some curves have not been saved” | Kullanıcı bu belirli uyarıyı **OK** ile geçmeyi onayladı. Kullanıcı için gerekli veriler CSV’ye yazılmış durumda. Uyarı açıkken Disconnect henüz uygulanmamış olabilir. OK sonrasında bağlantının veya ölçümün durumunu yeniden oku. Bir testte görülen “The potentiostat is null” ayrı bir hata ve her seferinde görünmedi. |
| Kontrol doğrulanmış biçimde kayıp | Gerçek modda gerekiyorsa seri Flexicon pompayı durdur ve duruşunu doğrula. Şüphe tek başına pompa durdurma sebebi değil. Kurtarmadan sonra pompayı yalnızca devam adımı gerektiriyorsa ve uygulanacak hız biliniyorsa çalıştır. Kesintinin süresi ne olursa olsun otomatik yıkama yapılmaz. |
| Kontrol geri kazanılamıyor veya devam koşulları doğrulanamıyor | Sınırlı yazılım ve bağlantı kurtarma denemeleri sonrasında nedeni kaydet ve reçeteyi güvenli biçimde durdur. Gerekirse operatör müdahalesi iste. Yeni ölçümü belirsiz eski ölçümün üzerine başlatma. |

**Reçete konumu.** Tamamlanmış döngüleri ve adımları koru. Kesilen adım bir Discrete PAD ölçüm beklemesiyse kalan süreyi yeniden oynatmadan sonraki adıma geç. Yarım kalmış DropView başlatma/durdurma, DropSens bağlanma ve diğer kontrol adımlarını atlama. Gerçek bir kesintiden sonraki devamda eski CSV’leri koru, eski oturumla ilişkili yeni oturum ve paket aç. Valflerin konumunu doğrula. Yalnızca editörü geri açıp aynı ölçümü sürdürürken paket değiştirme.

## Saha testleri ve gözlemler

| Deneme | Gözlem |
| --- | --- |
| Multiscript açıkken ölçümü elle durdurma | BTC abort etti ve üç adımlı test reçetesinde 3. adıma geçmedi. |
| Yalnızca Multiscript’i kapatma | BTC abort etti, fakat DropView grafikleri ve DropSens ölçümü devam etti. Kullanıcı aynı DropView oturumunda `Alt+S`, ardından `S` ile editörü açtı. Eski script çalışır durumdaydı. Elle Stop basınca düğme yeşile döndü. Pencere kapalıyken CSV’ye yazmanın sürdüğü bu testte ayrıca doğrulanmadı. |
| Ölçüm sırasında DropSens bağlantısını kesme | Bir denemede editör kapandı ve “The potentiostat is null” çıktı. Diğer denemede kaydedilmemiş eğriler uyarısı açıkken ölçüm devam etti ve BTC abort etmedi. OK sonrasında ölçüm durdu, bağlantı koptu ve BTC sonraki adıma geçmeden abort etti. İkinci denemede null hatası çıkmadı. |
| Ana DropView penceresini kapatma | BTC yaklaşık 5–10 saniyede pencere kaybı bildirip abort etti. Yeniden açıp bağlandıktan sonra editörde varsayılan script ve kırmızı düğme vardı. Bu testin yaklaşık süresi 15 saniyelik doğrulama kuralını tek başına sınamaz. |
| DropView çökmesi | Kullanıcı kendi kurulumunda DropSens LED’lerinden ölçümün durduğunu gördüğünü bildirdi. Hangi bağlantı yolunda ve hangi işlem sonlanma biçiminde bunun tekrarlandığı ayrıca kaydedilmeli. |

## Kodun devralınacak durumu

- Bağımsız watchdog, kalıcı kurtarma kaydı, bağlı yeni paket, sınırlı kurtarma denemeleri, Windows Update ertelemesi ve görsel sağlık kontrolleri mevcut. Windows gerçekten yeniden açılırsa kullanıcı oturumu ve uygulamanın başlatılması gerekebilir. Güncelleme ertelemesi doğrulanamazsa uyarı verilir.
- Simülasyon ve gerçek mod aynı algılama yolunu kullanıyor. Simülasyonun `observe_only` modu gerçek pompa müdahalesi, yazılımı otomatik yeniden açma veya yeni ölçüm başlatma yapmıyor. Bu nedenle sahada görülen abort, gerçek kurtarma zincirinin çalıştığını göstermez.
- `bench_test/watchdog/supervisor.py` içinde cihaz şüphesi için `DEVICE_FAULT_CONFIRM_SECONDS = 15` var. Doğrulama yeni bir örnek gerektiriyor. İzlenen DropView işleminin doğrulanmış çıkışı bu pencere beklemesine tabi değil. Multiscript'in kayıp/durmuş okumaları ayrıca ele alınıyor.
- `bench_test/dropview/automator.py` içindeki `restore_dropview_control` ve `bench_test/recipe/runner.py` içindeki kurtarma akışı başlangıç noktaları. **Saha testinden sonra onaylanan yeni Multiscript politikası henüz kodda uygulanmadı.** Mevcut akış kayıp editörde reçeteyi kesebiliyor ve aynı oturumda editörü açarak ölçümü korumuyor. `docs/watchdog_field_test_tr.md` tablosundaki Multiscript satırı mevcut eski davranışı anlatıyor.
- Kodda belirli kaydedilmemiş eğriler uyarısı için OK işleyicisi var. Ölçüm sırasındaki elle Disconnect akışında bu işleyicinin sürekli erişilebilir olduğu saha testinde görülmedi. Kapsamı yalnızca doğrulanmış DropView uyarısıyla sınırlı tut.
- Bir saha kaydında `supervisor_status.json` yazılırken `os.replace` üzerinde `[WinError 5]` görüldü. Sebebi doğrulanmadı. Durum yayınlamasının geçici dosya erişim hatasından nasıl etkilendiğini ayrı incele.

## Sonraki uygulama ve doğrulama sırası

1. **Önce Multiscript.** Aynı DropView işlemine ait editörü Load/Run/Stop göndermeden geri aç. Mevcut script ve veri akışı sağlıklıysa aynı adımı ve paketi koru. Editör geri gelmezse komut sınırında beklemeyi ve doğrulanmış kontrol kaybına geçişi uygula. Örtülü pencere, boş editör ve çalışan eski script durumlarını ayır.
2. **Sonra DropView çıkışı ve bağlantı.** Eski işlemin kimliğini ve doğrulanmış çıkışını kurtarma kaydına taşı. Kullanılan fiziksel bağlantı yolu için DropSens’in gerçekten durduğunu ayrı saha testiyle doğrula. DropView’u açma, DropSens’e bağlanma, doğru reçete adımı ve gerektiğinde scripti yeniden yükleyip yeni CSV yazma zincirini sına. İşlemin yanıt vermemesi ile sonlandığını ayrı tut.
3. **Uyarı ve regresyon.** Bilinen eğri uyarısını doğru DropView penceresinde otomatik geç, ardından sonucu yeniden oku. Yeniden açma testinde eski CSV’nin gerçekten ilerlediğini kontrol et. Erişilebilir otomatik testleri çalıştır. Seri Flexicon durdurma ve yeniden başlatma testini canlı donanım gelince tamamla.

## Git ve doğrulama kaydı

- Dal `feature/measurement-package`, `origin/feature/measurement-package` dalının üç commit önünde. Uzaktaki geri dönüş noktası `406bb66`. Yereldeki üç commit `4a19435` (kurtarma kaydı), `50b4e11` (bağlı devam paketi), `3407d08` (watchdog ve Windows Update ertelemesi).
- Son saha hazırlıkları ve 15 saniye değişikliği çalışma ağacında. Kullanıcının SOP belgesi ve pompa simülasyonu dahil mevcut değişikliklerini koru. `.codex_pycache/` ve `bench_test_log.txt` çalışma ağacında görünüyor. Commit öncesinde kapsamı seçerek ekle. Kullanıcı istemedikçe commit veya push yapma.
- 15 saniye değişikliğinden önce 171 otomatik test geçti. Sonrasında `git diff --check` geçti, ancak test çalıştırma isteği otomatik onay kotası nedeniyle reddedildi. Son değişikliklerden sonraki test sonucunu yeniden doğrula.
- İlk inceleme için `docs/watchdog_recovery.md`, `docs/watchdog_field_test_tr.md`, `tools/test_watchdog.py`, `tools/test_recipe_recovery.py` ve `tools/test_recovery_executor.py`. Saha kayıtları için `watchdog.log`, eski pakette `logs/recipe_recovery.json` ve yeni pakette `logs/recovery_origin.json`.
