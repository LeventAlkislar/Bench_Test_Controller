# Discrete PAD watchdog saha doğrulaması

İlk denemeyi, sonuçları deney verisi olarak kullanılmayacak kısa bir Discrete PAD
reçetesiyle yapın. Gerçek çalışma ortamındaki DropView sürümünü, bağlantı yolunu ve
seri Flexicon'u kullanın. İlk ölçümün CSV yazdığını ve durum çubuğunda son
bağlantı, son tarama ve son geçerli veri zamanlarının güncellendiğini doğrulayın.

Ölçümün normal süresi, sınanacak arızanın doğrulama ve kurtarma süresinden uzun
olsun. Başarılı devamı kontrol etmek için reçetede bir ölçüm beklemesi, ardından
ölçümün devam etmesi gereken bir adım ve son olarak planlı Stop/Exit bulunsun.
Her denemeye ayrı paketle başlayın.

| Deneme | Beklenen sonuç |
| --- | --- |
| Multiscript'i örtün veya küçültün | Görsel okuma için hedef pencere öne getirilmeye çalışılır. Örtülme veya küçültülme pompayı durdurmaz ve kurtarma başlatmaz. |
| DropView içinden ölçümü elle durdurun, editörü açık bırakın | Üç ardışık durmuş okumada reçete ilerleyişi kesilir. Gerçek modda pompanın duruşu doğrulanır; eski ölçüm ve valfler doğrulanırsa yeni pakette, doğru adımdan devam edilir. |
| Ölçüm sırasında Multiscript'i kapatın | Üç ardışık kayıp okumada, CSV gelmeye devam etse bile reçete kesilir. Eski ölçüm durumu doğrulanamazsa yeni Start gönderilmez. |
| DropSens bağlantısını test için kesin, sonra geri getirin | İlk kopukluk okumasından en az 15 saniye sonra gelen yeni okuma da kopukluğu doğrularsa reçete kesilir. Kayıp COM portu atlanır. Yeniden bağlantı ve eski ölçüm doğrulanırsa devam edilir; aksi durumda neden gösterilir. |
| DropView işleminin ölçüm sırasında kapanmasını sınayın | İzlenen işlemin sonlanması doğrulanırsa pencere kaybı için ek bekleme yoktur. DropView açılır ve bağlantı denenir. Yeni oturumun bağlı görünmesi, eski ölçümün durduğu kanıtı olarak kullanılmaz. |
| Bench Test Controller'ın beklenmedik çıkışını sınayın | Geçerli kayıt varsa watchdog uygulamayı yeniden açmayı dener. Yeni çalışan, seri bağlantı kurulabildiğinde pompa duruşunu doğrulamadan ölçüm başlatmaz. Windows yeniden başlatılması bu denemenin parçası değildir. |
| Kurtarma sırasında pompa seri yanıtını alamama durumu | Duruş doğrulanamadığında ölçüm başlatılmaz; logda pompanın durduğu iddia edilmez. |
| Başarılı devam için ayrı ayrı 0, pozitif ve boş RPM | 0'da pompa çalışmaz. Pozitif değer uygulanır. Boş alanda bilinen devreden hız kullanılır; hız bilinmiyorsa pompa kapalı kalır. Hiçbir durumda otomatik yıkama yapılmaz. |
| Bir kurtarma isteği beklerken uygulamanın Stop düğmesine basın | Kullanıcı durdurması önceliklidir. Bekleyen komut reçeteyi yeniden açmaz. |
| Simülasyonda bağlantı veya ölçüm durması | BTC aynı algılama ve kurtarma akışını uygular. Gerekirse DropView veya BTC yeniden başlar ve Discrete PAD yeni pakette devam eder. Pompa ve valf komutları atlanır, DropView ve DropSens gerçek çalışır. |

Öncelikli geçiş ölçütü: **DropView kapandıktan sonra yeniden açılan oturumdan eski
ölçümün durduğu doğrulanabiliyor mu?** Doğrulanamıyorsa yeni ölçümün başlamaması
beklenen güvenlik davranışıdır; bu senaryo gözetimsiz devam açısından doğrulanmış
sayılmaz. Üretimde kullanılacak Windows hesabıyla güncelleme ertelemesini ve
geri yüklenmesini ayrıca doğrulayın.

Her denemede şunları kaydedin: arızanın saati, doğrulama saati, pompa duruş
sonucu, son eski CSV, devam edilen döngü/adım, ilk yeni CSV ve varsa engel nedeni.
Karşılaştırma için eski ve yeni paketteki `logs/recipe_recovery.json`,
yenideki `logs/recovery_origin.json` ve watchdog çalışma dizinindeki
`watchdog.log` yeterli başlangıç noktalarıdır. Eski CSV'ler korunmalı; yeni CSV'ler
yeni pakete yazılmalı ve eski ölçüm beklemesinin kalan süresi tekrar edilmemelidir.
