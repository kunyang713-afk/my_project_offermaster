package com.offermaster.service;

import org.springframework.stereotype.Service;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

@Service
public class MaskingService {

    private static final Pattern PHONE =
            Pattern.compile("(\\+?86[- ]?)?(1[3-9]\\d)(\\d{5})(\\d{4})");
    private static final Pattern EMAIL =
            Pattern.compile("\\b([\\w.+-]{1,3})[\\w.+-]*@([\\w-]+\\.[\\w.]+)\\b");

    public String mask(String text) {
        if (text == null) return "";
        String out = text;
        Matcher phoneMatcher = PHONE.matcher(out);
        out = phoneMatcher.replaceAll("$1$2*****$4");
        Matcher emailMatcher = EMAIL.matcher(out);
        out = emailMatcher.replaceAll("$1***@$2");
        return out;
    }
}
