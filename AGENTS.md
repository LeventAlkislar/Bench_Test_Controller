
# AGENTS.md

## Dil ve İletişim
- Kullanıcı Türkçe yazıyorsa Türkçe yanıt ver.
- Kısa, net ve uygulanabilir açıklamalar yap.
- Değişiklik yapmadan önce neyi değiştireceğini kısaca belirt.
- Belirsiz veya riskli kararlar varsa önce kullanıcıdan onay al.

## Genel Çalışma Kuralları
- Kullanıcının mevcut değişikliklerini geri alma.
- `git reset --hard`, `git checkout --`, toplu silme veya yıkıcı işlemler yapma; gerekirse önce açık onay iste.
- Dosya düzenlemelerinde mümkün olduğunca küçük ve hedefli değişiklikler yap.
- Mevcut proje yapısını, isimlendirme stilini ve kod düzenini koru.
- Gereksiz refactor yapma; sadece istenen görevle ilişkili değişiklikleri uygula.

## Proje Yapısı
- Ana giriş dosyası: `main.py`
- Uygulama kodları: `bench_test/`
- Arayüz dosyaları: `ui/`
- Build ve dağıtım çıktıları: `build/`, `dist/`
- Sanal ortam: `.venv/`

## Kodlama Kuralları
- Python kodunda okunabilir, sade ve bakım yapılabilir çözümler tercih et.
- Yeni kod eklerken mevcut modül yapısına uy.
- Karmaşık mantık varsa kısa açıklayıcı yorum ekle; bariz kodlara yorum ekleme.
- Hata yönetimini sessizce geçiştirme; kullanıcıya veya loglara anlamlı bilgi ver.
- UI tarafında mevcut tasarım dilini ve kullanıcı akışını koru.

## Test ve Doğrulama
- Değişiklikten sonra mümkünse ilgili testleri veya uygulama doğrulamasını çalıştır.
- Test çalıştırılamıyorsa nedenini final yanıtta belirt.
- Build çıktıları veya otomatik üretilen dosyalar özellikle istenmedikçe değiştirilmemeli.

## Git Kuralları
- Kullanıcı istemedikçe commit oluşturma.
- Kullanıcı istemedikçe branch değiştirme veya yeni branch açma.
- Mevcut branch ve çalışma ağacındaki kullanıcı değişikliklerine saygı göster.

## Genel Kurallar
1. Analysis: Make architectural analysis before diving into implementation
2. Confirm design options explicitly before coding begins
3. Work incrementally: one phase or feature at a time, wait for confirmation before proceeding
4. Use PyCharm on Windows; that is Windows-compatible terminal commands (e.g., type nul > rather than touch)
5. Ask for sample files (e.g., log files, script files) to validate parser implementations against real data
6. Değişiklikleri benim ile tartışmadan ve izin almadan kod üzerinde uygulama. Ben başlama onayı için sor, onay verdiğim zaman başla.
8. Sana verdiğim her yeni kod parçası veya güncellenmiş dosya, o konudaki tek 'doğru' (source of truth) kabul et. Eski dosyalardaki yöntemler ile yeni önerdiğim yapılar çelişirse, her zaman en son verdiğim bilgiyi esas al.
9. Önemli ve karmaşık güncelleme çalışmalarına başlamadan ya da bütün güncellemeleri tamamladıktan sonra git commit etmeyi hatırlat.
10. Kod incelemelerini yaparken yapısal kötü kokular almaya başladığında refactor yapmayı değerlendirmek üzere öneride bulun.