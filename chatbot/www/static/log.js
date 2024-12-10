function refresh(msg) {
    $.ajax({
        url: '/getlog',
        type: "GET",
        data: $.param({'validate': getCookie('validation')}),
        success: function(res) {
            var data = JSON.parse(res);            
            if (data.code == 0) {
                let log = data.log;
                $('#log-input').text(log);
                var scrollHeight = $('#log-input').prop("scrollHeight");
                $('#log-input').scrollTop(scrollHeight, 200);
                $('button#REFRESH').on('click', function(e) {
                    refresh();
                });
            } else {
                toastr.error(data.message, '日志读取失败');
            }
        },
        error: function() {
            toastr.error('服务器异常', '日志读取失败');
        }
    });   
}

function apiBase(uri){
    return `/chat-robot/api${uri}`
}
/**
 * 开启
 */
function startDetectLog() {
    $.ajax({
        url: apiBase('/detect/log-on'),
        type: "POST",
        data: $.param({'validate': getCookie('validation')}),
        success: function(resp) {
            var res = JSON.parse(resp);            
            if (res.code == 0) {
                toastr.success(res.message, '开启成功');
            } else {
                toastr.error(res.message, '开启失败');
            }
        },
        error: function() {
            toastr.error('服务器异常', '开启失败');
        }
    });   
}
/**
 * 关闭
 */
function stopDetectLog() {
    $.ajax({
        url: apiBase('/detect/log-off'),
        type: "POST",
        data: $.param({'validate': getCookie('validation')}),
        success: function(resp) {
            var res = JSON.parse(resp);            
            if (res.code == 0) {
                toastr.success(res.message, '关闭成功');
            } else {
                toastr.error(res.message, '关闭失败');
            }
        },
        error: function() {
            toastr.error('服务器异常', '关闭失败');
        }
    });   
}

$(function() {
    refresh();
    $('button#startDetectLog').on('click', function(e) {startDetectLog();});
    $('button#stopDetectLog').on('click', function(e) {stopDetectLog();});
});

