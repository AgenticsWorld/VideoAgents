import {readFileSync} from 'node:fs'
import path from 'node:path'

/**
 * 桌面端更新弹窗 / 进度窗的多语言文案。语言与网页界面一致(static/i18n/i18n.js 的 12 种),
 * 读 state.json 的 ui_lang(旧版回落 genconfig.json 的 ui_language,同 core.ui_lang_code);
 * 未设置或不在支持列表内时用英文。
 */
export const UPDATE_LANGUAGES = ['en', 'zh', 'ja', 'ko', 'vi', 'es', 'fr', 'de', 'id', 'pt', 'ru', 'ar'] as const
export type UpdateLanguage = typeof UPDATE_LANGUAGES[number]

export interface UpdateStrings {
  availableTitle: string
  /** {version} */
  availableMessage: string
  /** {current} */
  availableDetail: string
  installNow: string
  installNextLaunch: string
  notNow: string
  progressTitle: string
  progressNote: string
  progressWindowTitle: string
  checking: string
  downloading: string
  preparing: string
  installerStarted: string
  failedTitle: string
  requiredTitle: string
  requiredMessage: string
  requiredRetryMessage: string
  /** {current} {minimum} {version} */
  requiredDetail: string
  /** {current} {minimum} */
  requiredRetryDetail: string
  updateNow: string
  quit: string
  requiredProgressTitle: string
  requiredProgressNote: string
  requiredProgressWindowTitle: string
  pendingFailedTitle: string
  /** {current} */
  pendingFailedDetail: string
}

export const UPDATE_STRINGS: Record<UpdateLanguage, UpdateStrings> = {
  en: {
    availableTitle: 'VideoAgents Update Available',
    availableMessage: 'VideoAgents {version} is available.',
    availableDetail: 'You are using version {current}.\n'
      + '“Install Now” downloads and installs the update right away, then restarts the app.\n'
      + '“Install on Next Launch” downloads it in the background while you keep working,'
      + ' and installs it the next time you open the app.',
    installNow: 'Install Now',
    installNextLaunch: 'Install on Next Launch',
    notNow: 'Not Now',
    progressTitle: 'Updating VideoAgents',
    progressNote: 'The app will install the update and restart when the download finishes.',
    progressWindowTitle: 'VideoAgents Update',
    checking: 'Checking for updates…',
    downloading: 'Downloading the VideoAgents update…',
    preparing: 'Preparing the update…',
    installerStarted: 'The installer has started. The app will close and restart…',
    failedTitle: 'VideoAgents Update Failed',
    requiredTitle: 'VideoAgents Update Required',
    requiredMessage: 'This version is no longer supported',
    requiredRetryMessage: 'The update failed. Please try again.',
    requiredDetail: 'Your version {current} is older than the minimum supported version {minimum}.'
      + ' Update to {version} to keep using VideoAgents.',
    requiredRetryDetail: 'Current version {current}; minimum supported version {minimum}.',
    updateNow: 'Update Now',
    quit: 'Quit',
    requiredProgressTitle: 'Installing the Required VideoAgents Update',
    requiredProgressNote: 'VideoAgents can’t be used until the update is installed and the app restarts.',
    requiredProgressWindowTitle: 'VideoAgents Update Required',
    pendingFailedTitle: 'VideoAgents Update Not Installed',
    pendingFailedDetail: 'VideoAgents will start with the current version {current}.',
  },
  zh: {
    availableTitle: '发现 VideoAgents 更新',
    availableMessage: '发现 VideoAgents {version} 新版本。',
    availableDetail: '当前版本 {current}。\n'
      + '「立即安装」：马上下载并安装，完成后自动重新启动。\n'
      + '「下次启动时安装」：在后台下载，不打断当前使用，下次打开应用时自动安装。',
    installNow: '立即安装',
    installNextLaunch: '下次启动时安装',
    notNow: '暂不更新',
    progressTitle: '正在更新 VideoAgents',
    progressNote: '下载完成后应用会自动安装并重新启动。',
    progressWindowTitle: 'VideoAgents 更新',
    checking: '正在检查更新…',
    downloading: '正在下载 VideoAgents 更新…',
    preparing: '正在准备应用更新…',
    installerStarted: '安装程序已启动，应用即将关闭并自动重启…',
    failedTitle: 'VideoAgents 更新失败',
    requiredTitle: '必须更新 VideoAgents',
    requiredMessage: '当前版本已停止支持',
    requiredRetryMessage: '更新失败，请重试',
    requiredDetail: '当前版本 {current} 低于最低可用版本 {minimum}。 必须更新到 {version} 后才能继续使用。',
    requiredRetryDetail: '当前版本 {current}，最低可用版本 {minimum}。',
    updateNow: '立即更新',
    quit: '退出应用',
    requiredProgressTitle: '正在强制更新 VideoAgents',
    requiredProgressNote: '更新完成并重新启动前，不能使用应用的其他功能。',
    requiredProgressWindowTitle: 'VideoAgents 必须更新',
    pendingFailedTitle: 'VideoAgents 更新安装失败',
    pendingFailedDetail: '应用将以当前版本 {current} 继续启动。',
  },
  ja: {
    availableTitle: 'VideoAgents のアップデートがあります',
    availableMessage: 'VideoAgents {version} が利用可能です。',
    availableDetail: '現在のバージョン：{current}\n'
      + '「今すぐインストール」：すぐにダウンロードしてインストールし、完了後にアプリを再起動します。\n'
      + '「次回起動時にインストール」：作業を中断せずにバックグラウンドでダウンロードし、次にアプリを開いたときにインストールします。',
    installNow: '今すぐインストール',
    installNextLaunch: '次回起動時にインストール',
    notNow: '後で',
    progressTitle: 'VideoAgents をアップデートしています',
    progressNote: 'ダウンロードが完了すると、アップデートをインストールしてアプリを再起動します。',
    progressWindowTitle: 'VideoAgents アップデート',
    checking: 'アップデートを確認しています…',
    downloading: 'VideoAgents のアップデートをダウンロードしています…',
    preparing: 'アップデートを準備しています…',
    installerStarted: 'インストーラーを起動しました。アプリを終了して再起動します…',
    failedTitle: 'VideoAgents のアップデートに失敗しました',
    requiredTitle: 'VideoAgents のアップデートが必要です',
    requiredMessage: 'このバージョンはサポートが終了しました',
    requiredRetryMessage: 'アップデートに失敗しました。もう一度お試しください。',
    requiredDetail: '現在のバージョン {current} は、サポートされる最低バージョン {minimum} より古いため、'
      + '引き続き使用するには {version} へのアップデートが必要です。',
    requiredRetryDetail: '現在のバージョン：{current}、サポートされる最低バージョン：{minimum}',
    updateNow: '今すぐアップデート',
    quit: '終了',
    requiredProgressTitle: '必須アップデートをインストールしています',
    requiredProgressNote: 'アップデートのインストールとアプリの再起動が終わるまで、ほかの機能は使用できません。',
    requiredProgressWindowTitle: 'VideoAgents の必須アップデート',
    pendingFailedTitle: 'VideoAgents のアップデートをインストールできませんでした',
    pendingFailedDetail: '現在のバージョン {current} で起動します。',
  },
  ko: {
    availableTitle: 'VideoAgents 업데이트 있음',
    availableMessage: 'VideoAgents {version} 버전을 사용할 수 있습니다.',
    availableDetail: '현재 버전: {current}\n'
      + '‘지금 설치’: 바로 다운로드하여 설치하고, 완료되면 앱을 다시 시작합니다.\n'
      + '‘다음 실행 시 설치’: 작업을 방해하지 않고 백그라운드에서 다운로드한 뒤, 다음에 앱을 열 때 설치합니다.',
    installNow: '지금 설치',
    installNextLaunch: '다음 실행 시 설치',
    notNow: '나중에',
    progressTitle: 'VideoAgents 업데이트 중',
    progressNote: '다운로드가 끝나면 업데이트를 설치하고 앱을 다시 시작합니다.',
    progressWindowTitle: 'VideoAgents 업데이트',
    checking: '업데이트 확인 중…',
    downloading: 'VideoAgents 업데이트 다운로드 중…',
    preparing: '업데이트 준비 중…',
    installerStarted: '설치 프로그램이 시작되었습니다. 앱이 종료된 후 다시 시작됩니다…',
    failedTitle: 'VideoAgents 업데이트 실패',
    requiredTitle: 'VideoAgents 업데이트 필요',
    requiredMessage: '이 버전은 더 이상 지원되지 않습니다',
    requiredRetryMessage: '업데이트에 실패했습니다. 다시 시도해 주세요.',
    requiredDetail: '현재 버전 {current}은(는) 최소 지원 버전 {minimum}보다 낮습니다.'
      + ' 계속 사용하려면 {version}(으)로 업데이트해야 합니다.',
    requiredRetryDetail: '현재 버전 {current}, 최소 지원 버전 {minimum}',
    updateNow: '지금 업데이트',
    quit: '종료',
    requiredProgressTitle: '필수 VideoAgents 업데이트 설치 중',
    requiredProgressNote: '업데이트 설치와 앱 재시작이 끝날 때까지 다른 기능을 사용할 수 없습니다.',
    requiredProgressWindowTitle: 'VideoAgents 필수 업데이트',
    pendingFailedTitle: 'VideoAgents 업데이트를 설치하지 못했습니다',
    pendingFailedDetail: '현재 버전 {current}(으)로 시작합니다.',
  },
  vi: {
    availableTitle: 'Có bản cập nhật VideoAgents',
    availableMessage: 'Đã có VideoAgents {version}.',
    availableDetail: 'Phiên bản hiện tại: {current}.\n'
      + '“Cài đặt ngay”: tải xuống và cài đặt ngay, sau đó khởi động lại ứng dụng.\n'
      + '“Cài đặt ở lần khởi động sau”: tải xuống trong nền mà không làm gián đoạn công việc,'
      + ' và cài đặt vào lần mở ứng dụng tiếp theo.',
    installNow: 'Cài đặt ngay',
    installNextLaunch: 'Cài đặt ở lần khởi động sau',
    notNow: 'Để sau',
    progressTitle: 'Đang cập nhật VideoAgents',
    progressNote: 'Khi tải xuống xong, ứng dụng sẽ cài đặt bản cập nhật và khởi động lại.',
    progressWindowTitle: 'Cập nhật VideoAgents',
    checking: 'Đang kiểm tra bản cập nhật…',
    downloading: 'Đang tải bản cập nhật VideoAgents…',
    preparing: 'Đang chuẩn bị bản cập nhật…',
    installerStarted: 'Trình cài đặt đã khởi chạy. Ứng dụng sẽ đóng và khởi động lại…',
    failedTitle: 'Cập nhật VideoAgents thất bại',
    requiredTitle: 'Bắt buộc cập nhật VideoAgents',
    requiredMessage: 'Phiên bản này không còn được hỗ trợ',
    requiredRetryMessage: 'Cập nhật thất bại. Vui lòng thử lại.',
    requiredDetail: 'Phiên bản hiện tại {current} thấp hơn phiên bản tối thiểu được hỗ trợ {minimum}.'
      + ' Hãy cập nhật lên {version} để tiếp tục sử dụng VideoAgents.',
    requiredRetryDetail: 'Phiên bản hiện tại {current}; phiên bản tối thiểu được hỗ trợ {minimum}.',
    updateNow: 'Cập nhật ngay',
    quit: 'Thoát',
    requiredProgressTitle: 'Đang cài đặt bản cập nhật bắt buộc',
    requiredProgressNote: 'Không thể sử dụng VideoAgents cho đến khi bản cập nhật được cài đặt và ứng dụng khởi động lại.',
    requiredProgressWindowTitle: 'Bắt buộc cập nhật VideoAgents',
    pendingFailedTitle: 'Không thể cài đặt bản cập nhật VideoAgents',
    pendingFailedDetail: 'VideoAgents sẽ khởi động với phiên bản hiện tại {current}.',
  },
  es: {
    availableTitle: 'Actualización de VideoAgents disponible',
    availableMessage: 'VideoAgents {version} está disponible.',
    availableDetail: 'Versión actual: {current}.\n'
      + '«Instalar ahora»: descarga e instala la actualización de inmediato y reinicia la aplicación.\n'
      + '«Instalar en el próximo inicio»: la descarga en segundo plano sin interrumpir tu trabajo'
      + ' y la instala la próxima vez que abras la aplicación.',
    installNow: 'Instalar ahora',
    installNextLaunch: 'Instalar en el próximo inicio',
    notNow: 'Ahora no',
    progressTitle: 'Actualizando VideoAgents',
    progressNote: 'Cuando termine la descarga, la aplicación instalará la actualización y se reiniciará.',
    progressWindowTitle: 'Actualización de VideoAgents',
    checking: 'Buscando actualizaciones…',
    downloading: 'Descargando la actualización de VideoAgents…',
    preparing: 'Preparando la actualización…',
    installerStarted: 'El instalador se ha iniciado. La aplicación se cerrará y se reiniciará…',
    failedTitle: 'Error al actualizar VideoAgents',
    requiredTitle: 'Actualización obligatoria de VideoAgents',
    requiredMessage: 'Esta versión ya no es compatible',
    requiredRetryMessage: 'La actualización ha fallado. Inténtalo de nuevo.',
    requiredDetail: 'Tu versión {current} es anterior a la versión mínima compatible {minimum}.'
      + ' Actualiza a {version} para seguir usando VideoAgents.',
    requiredRetryDetail: 'Versión actual: {current}; versión mínima compatible: {minimum}.',
    updateNow: 'Actualizar ahora',
    quit: 'Salir',
    requiredProgressTitle: 'Instalando la actualización obligatoria',
    requiredProgressNote: 'No podrás usar VideoAgents hasta que se instale la actualización y se reinicie la aplicación.',
    requiredProgressWindowTitle: 'Actualización obligatoria de VideoAgents',
    pendingFailedTitle: 'No se pudo instalar la actualización de VideoAgents',
    pendingFailedDetail: 'VideoAgents se iniciará con la versión actual {current}.',
  },
  fr: {
    availableTitle: 'Mise à jour de VideoAgents disponible',
    availableMessage: 'VideoAgents {version} est disponible.',
    availableDetail: 'Version actuelle : {current}.\n'
      + '« Installer maintenant » : télécharge et installe la mise à jour immédiatement, puis redémarre l’application.\n'
      + '« Installer au prochain lancement » : la télécharge en arrière-plan sans vous interrompre'
      + ' et l’installe à la prochaine ouverture de l’application.',
    installNow: 'Installer maintenant',
    installNextLaunch: 'Installer au prochain lancement',
    notNow: 'Plus tard',
    progressTitle: 'Mise à jour de VideoAgents en cours',
    progressNote: 'Une fois le téléchargement terminé, l’application installera la mise à jour et redémarrera.',
    progressWindowTitle: 'Mise à jour de VideoAgents',
    checking: 'Recherche de mises à jour…',
    downloading: 'Téléchargement de la mise à jour de VideoAgents…',
    preparing: 'Préparation de la mise à jour…',
    installerStarted: 'Le programme d’installation a démarré. L’application va se fermer puis redémarrer…',
    failedTitle: 'Échec de la mise à jour de VideoAgents',
    requiredTitle: 'Mise à jour de VideoAgents obligatoire',
    requiredMessage: 'Cette version n’est plus prise en charge',
    requiredRetryMessage: 'La mise à jour a échoué. Veuillez réessayer.',
    requiredDetail: 'Votre version {current} est antérieure à la version minimale prise en charge ({minimum}).'
      + ' Mettez à jour vers {version} pour continuer à utiliser VideoAgents.',
    requiredRetryDetail: 'Version actuelle : {current} ; version minimale prise en charge : {minimum}.',
    updateNow: 'Mettre à jour',
    quit: 'Quitter',
    requiredProgressTitle: 'Installation de la mise à jour obligatoire',
    requiredProgressNote: 'VideoAgents ne peut pas être utilisé tant que la mise à jour n’est pas installée'
      + ' et que l’application n’a pas redémarré.',
    requiredProgressWindowTitle: 'Mise à jour obligatoire de VideoAgents',
    pendingFailedTitle: 'Impossible d’installer la mise à jour de VideoAgents',
    pendingFailedDetail: 'VideoAgents va démarrer avec la version actuelle {current}.',
  },
  de: {
    availableTitle: 'VideoAgents-Update verfügbar',
    availableMessage: 'VideoAgents {version} ist verfügbar.',
    availableDetail: 'Aktuelle Version: {current}.\n'
      + '„Jetzt installieren“: Das Update wird sofort heruntergeladen und installiert, anschließend startet die App neu.\n'
      + '„Beim nächsten Start installieren“: Das Update wird im Hintergrund heruntergeladen, ohne Sie zu unterbrechen,'
      + ' und beim nächsten Öffnen der App installiert.',
    installNow: 'Jetzt installieren',
    installNextLaunch: 'Beim nächsten Start installieren',
    notNow: 'Später',
    progressTitle: 'VideoAgents wird aktualisiert',
    progressNote: 'Nach dem Download wird das Update installiert und die App neu gestartet.',
    progressWindowTitle: 'VideoAgents-Update',
    checking: 'Suche nach Updates …',
    downloading: 'VideoAgents-Update wird heruntergeladen …',
    preparing: 'Update wird vorbereitet …',
    installerStarted: 'Das Installationsprogramm wurde gestartet. Die App wird geschlossen und neu gestartet …',
    failedTitle: 'VideoAgents-Update fehlgeschlagen',
    requiredTitle: 'VideoAgents-Update erforderlich',
    requiredMessage: 'Diese Version wird nicht mehr unterstützt',
    requiredRetryMessage: 'Das Update ist fehlgeschlagen. Bitte versuchen Sie es erneut.',
    requiredDetail: 'Ihre Version {current} ist älter als die mindestens unterstützte Version {minimum}.'
      + ' Aktualisieren Sie auf {version}, um VideoAgents weiter zu verwenden.',
    requiredRetryDetail: 'Aktuelle Version {current}; mindestens unterstützte Version {minimum}.',
    updateNow: 'Jetzt aktualisieren',
    quit: 'Beenden',
    requiredProgressTitle: 'Erforderliches Update wird installiert',
    requiredProgressNote: 'VideoAgents kann erst wieder verwendet werden, wenn das Update installiert'
      + ' und die App neu gestartet ist.',
    requiredProgressWindowTitle: 'VideoAgents-Update erforderlich',
    pendingFailedTitle: 'VideoAgents-Update konnte nicht installiert werden',
    pendingFailedDetail: 'VideoAgents startet mit der aktuellen Version {current}.',
  },
  id: {
    availableTitle: 'Pembaruan VideoAgents tersedia',
    availableMessage: 'VideoAgents {version} sudah tersedia.',
    availableDetail: 'Versi saat ini: {current}.\n'
      + '“Instal sekarang”: unduh dan instal pembaruan sekarang juga, lalu aplikasi dimulai ulang.\n'
      + '“Instal saat dibuka berikutnya”: unduh di latar belakang tanpa mengganggu pekerjaan Anda,'
      + ' lalu instal saat aplikasi dibuka lagi.',
    installNow: 'Instal sekarang',
    installNextLaunch: 'Instal saat dibuka berikutnya',
    notNow: 'Nanti saja',
    progressTitle: 'Memperbarui VideoAgents',
    progressNote: 'Setelah unduhan selesai, aplikasi akan menginstal pembaruan dan memulai ulang.',
    progressWindowTitle: 'Pembaruan VideoAgents',
    checking: 'Memeriksa pembaruan…',
    downloading: 'Mengunduh pembaruan VideoAgents…',
    preparing: 'Menyiapkan pembaruan…',
    installerStarted: 'Penginstal telah dijalankan. Aplikasi akan ditutup dan dimulai ulang…',
    failedTitle: 'Pembaruan VideoAgents gagal',
    requiredTitle: 'VideoAgents wajib diperbarui',
    requiredMessage: 'Versi ini tidak lagi didukung',
    requiredRetryMessage: 'Pembaruan gagal. Silakan coba lagi.',
    requiredDetail: 'Versi Anda {current} lebih lama dari versi minimum yang didukung {minimum}.'
      + ' Perbarui ke {version} untuk terus menggunakan VideoAgents.',
    requiredRetryDetail: 'Versi saat ini {current}; versi minimum yang didukung {minimum}.',
    updateNow: 'Perbarui sekarang',
    quit: 'Keluar',
    requiredProgressTitle: 'Menginstal pembaruan wajib',
    requiredProgressNote: 'VideoAgents tidak dapat digunakan sampai pembaruan terinstal dan aplikasi dimulai ulang.',
    requiredProgressWindowTitle: 'Pembaruan wajib VideoAgents',
    pendingFailedTitle: 'Pembaruan VideoAgents tidak dapat diinstal',
    pendingFailedDetail: 'VideoAgents akan dimulai dengan versi saat ini {current}.',
  },
  pt: {
    availableTitle: 'Atualização do VideoAgents disponível',
    availableMessage: 'O VideoAgents {version} está disponível.',
    availableDetail: 'Versão atual: {current}.\n'
      + '“Instalar agora”: baixa e instala a atualização imediatamente e reinicia o aplicativo.\n'
      + '“Instalar na próxima abertura”: baixa em segundo plano sem interromper seu trabalho'
      + ' e instala na próxima vez que você abrir o aplicativo.',
    installNow: 'Instalar agora',
    installNextLaunch: 'Instalar na próxima abertura',
    notNow: 'Agora não',
    progressTitle: 'Atualizando o VideoAgents',
    progressNote: 'Quando o download terminar, o aplicativo instalará a atualização e será reiniciado.',
    progressWindowTitle: 'Atualização do VideoAgents',
    checking: 'Verificando atualizações…',
    downloading: 'Baixando a atualização do VideoAgents…',
    preparing: 'Preparando a atualização…',
    installerStarted: 'O instalador foi iniciado. O aplicativo será fechado e reiniciado…',
    failedTitle: 'Falha ao atualizar o VideoAgents',
    requiredTitle: 'Atualização obrigatória do VideoAgents',
    requiredMessage: 'Esta versão não é mais compatível',
    requiredRetryMessage: 'A atualização falhou. Tente novamente.',
    requiredDetail: 'Sua versão {current} é anterior à versão mínima compatível {minimum}.'
      + ' Atualize para a {version} para continuar usando o VideoAgents.',
    requiredRetryDetail: 'Versão atual {current}; versão mínima compatível {minimum}.',
    updateNow: 'Atualizar agora',
    quit: 'Sair',
    requiredProgressTitle: 'Instalando a atualização obrigatória',
    requiredProgressNote: 'O VideoAgents não pode ser usado até que a atualização seja instalada e o aplicativo reiniciado.',
    requiredProgressWindowTitle: 'Atualização obrigatória do VideoAgents',
    pendingFailedTitle: 'Não foi possível instalar a atualização do VideoAgents',
    pendingFailedDetail: 'O VideoAgents será iniciado com a versão atual {current}.',
  },
  ru: {
    availableTitle: 'Доступно обновление VideoAgents',
    availableMessage: 'Доступна версия VideoAgents {version}.',
    availableDetail: 'Текущая версия: {current}.\n'
      + '«Установить сейчас»: обновление будет сразу загружено и установлено, затем приложение перезапустится.\n'
      + '«Установить при следующем запуске»: обновление загрузится в фоне, не мешая работе,'
      + ' и установится при следующем открытии приложения.',
    installNow: 'Установить сейчас',
    installNextLaunch: 'Установить при следующем запуске',
    notNow: 'Не сейчас',
    progressTitle: 'Обновление VideoAgents',
    progressNote: 'После загрузки приложение установит обновление и перезапустится.',
    progressWindowTitle: 'Обновление VideoAgents',
    checking: 'Проверка обновлений…',
    downloading: 'Загрузка обновления VideoAgents…',
    preparing: 'Подготовка обновления…',
    installerStarted: 'Установщик запущен. Приложение закроется и перезапустится…',
    failedTitle: 'Не удалось обновить VideoAgents',
    requiredTitle: 'Требуется обновление VideoAgents',
    requiredMessage: 'Эта версия больше не поддерживается',
    requiredRetryMessage: 'Не удалось выполнить обновление. Попробуйте ещё раз.',
    requiredDetail: 'Ваша версия {current} ниже минимальной поддерживаемой версии {minimum}.'
      + ' Чтобы продолжить работу с VideoAgents, обновитесь до {version}.',
    requiredRetryDetail: 'Текущая версия: {current}; минимальная поддерживаемая версия: {minimum}.',
    updateNow: 'Обновить сейчас',
    quit: 'Выйти',
    requiredProgressTitle: 'Установка обязательного обновления',
    requiredProgressNote: 'VideoAgents нельзя использовать, пока обновление не будет установлено и приложение не перезапустится.',
    requiredProgressWindowTitle: 'Обязательное обновление VideoAgents',
    pendingFailedTitle: 'Не удалось установить обновление VideoAgents',
    pendingFailedDetail: 'VideoAgents запустится с текущей версией {current}.',
  },
  ar: {
    availableTitle: 'يتوفر تحديث لـ VideoAgents',
    availableMessage: 'يتوفر الإصدار {version} من VideoAgents.',
    availableDetail: 'الإصدار الحالي: {current}.\n'
      + '«التثبيت الآن»: تنزيل التحديث وتثبيته فورًا، ثم إعادة تشغيل التطبيق.\n'
      + '«التثبيت عند التشغيل التالي»: تنزيل التحديث في الخلفية دون مقاطعة عملك، وتثبيته عند فتح التطبيق في المرة القادمة.',
    installNow: 'التثبيت الآن',
    installNextLaunch: 'التثبيت عند التشغيل التالي',
    notNow: 'ليس الآن',
    progressTitle: 'جارٍ تحديث VideoAgents',
    progressNote: 'بعد اكتمال التنزيل، سيثبّت التطبيق التحديث ويُعاد تشغيله.',
    progressWindowTitle: 'تحديث VideoAgents',
    checking: 'جارٍ البحث عن تحديثات…',
    downloading: 'جارٍ تنزيل تحديث VideoAgents…',
    preparing: 'جارٍ تجهيز التحديث…',
    installerStarted: 'بدأ برنامج التثبيت. سيُغلق التطبيق ثم يُعاد تشغيله…',
    failedTitle: 'فشل تحديث VideoAgents',
    requiredTitle: 'يجب تحديث VideoAgents',
    requiredMessage: 'هذا الإصدار لم يعد مدعومًا',
    requiredRetryMessage: 'فشل التحديث. يُرجى المحاولة مرة أخرى.',
    requiredDetail: 'إصدارك الحالي {current} أقدم من أدنى إصدار مدعوم {minimum}.'
      + ' حدّث إلى {version} لمواصلة استخدام VideoAgents.',
    requiredRetryDetail: 'الإصدار الحالي {current}؛ أدنى إصدار مدعوم {minimum}.',
    updateNow: 'التحديث الآن',
    quit: 'إنهاء',
    requiredProgressTitle: 'جارٍ تثبيت التحديث الإلزامي',
    requiredProgressNote: 'لا يمكن استخدام VideoAgents حتى يكتمل تثبيت التحديث وإعادة تشغيل التطبيق.',
    requiredProgressWindowTitle: 'تحديث إلزامي لـ VideoAgents',
    pendingFailedTitle: 'تعذّر تثبيت تحديث VideoAgents',
    pendingFailedDetail: 'سيبدأ VideoAgents بالإصدار الحالي {current}.',
  },
}

function normalizeLanguage(value: unknown): UpdateLanguage | undefined {
  if (typeof value !== 'string' || !value) return undefined
  let code = value.toLowerCase().split(/[-_]/)[0]
  if (code === 'in') code = 'id'   // 旧版印尼语代码,与 i18n.js 一致
  return (UPDATE_LANGUAGES as readonly string[]).includes(code) ? code as UpdateLanguage : undefined
}

/** 网页界面当前语言;runtimeDir 为后端运行目录(<数据目录>/.videoagents)。读不到或不支持时返回 'en'。 */
export function desktopUiLanguage(runtimeDir: string): UpdateLanguage {
  const sources: Array<[string, string]> = [['state.json', 'ui_lang'], ['genconfig.json', 'ui_language']]
  for (const [file, key] of sources) {
    try {
      const value = JSON.parse(readFileSync(path.join(runtimeDir, file), 'utf8')) as Record<string, unknown>
      const code = normalizeLanguage(value?.[key])
      if (code) return code
    } catch {
      // 文件不存在或损坏:看下一个来源
    }
  }
  return 'en'
}

export function updateStrings(language: UpdateLanguage): UpdateStrings {
  return UPDATE_STRINGS[language] ?? UPDATE_STRINGS.en
}

export function fillTemplate(template: string, values: Record<string, string>): string {
  return template.replace(/\{(\w+)\}/g, (match, key: string) => values[key] ?? match)
}
